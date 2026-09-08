"""Config flow for the Yunkan integration.

Connecting is a two-part conversation. First the user gives us an address; then
we ask that server how it wants to be authorised:

* servers that support the authorization handoff take over from there — the
  user is sent to the server's own sign-in and authorization page (whatever
  method their account uses: password, one-time code, passkey, SSO) and
  approves there, and the server hands us back a long-lived API token. No
  password ever reaches Home Assistant, and an account with two-factor
  authentication turned on works like any other;
* older servers have no such page, so we fall back to asking for a username and
  password, exactly as this integration always has.

Re-authentication takes the same fork, so an entry created with a password on
an old server upgrades itself to a token the first time the server asks for a
reauth after being updated.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import secrets
from typing import Any
from urllib.parse import quote, urlencode

import voluptuous as vol
from yarl import URL

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.http import current_request
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    YunkanApiClient,
    YunkanApiError,
    YunkanAuthError,
    YunkanConnectionError,
    YunkanMfaRequiredError,
    YunkanSetupRequiredError,
    normalize_base_url,
)
from .const import (
    CONF_API_TOKEN,
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_USE_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_BASE_URL,
    DEFAULT_PANEL_TITLE,
    DEFAULT_SIDEBAR_PANEL,
    DEFAULT_SNAPSHOT_BBOX,
    DEFAULT_SNAPSHOT_CROP,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    HANDOFF_AUTHORIZE_PATH,
    HANDOFF_CALLBACK_PATH,
    HANDOFF_CLIENT_NAME,
    OPT_PANEL_TITLE,
    OPT_PANEL_URL,
    OPT_SIDEBAR_PANEL,
    OPT_SNAPSHOT_BBOX,
    OPT_SNAPSHOT_CROP,
)
from .handoff import (
    async_forget_handoff_state,
    async_register_handoff_state,
    async_register_handoff_view,
)

_LOGGER = logging.getLogger(__name__)

# Both credential forms render the password masked; a bare ``str`` would show
# the Yunkan account password in the clear while it is being typed.
_PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)


def _callback_redirect_uri(hass: HomeAssistant) -> str:
    """Return the absolute URL the authorization page must redirect back to.

    ``require_current_request`` first: the address that matters is the one the
    user is reaching Home Assistant on right now, because their browser is what
    follows the redirect. Only when that fails do we look at the configured
    URLs, and then only at one this same browser is demonstrably using.
    """
    try:
        base = get_url(
            hass,
            require_current_request=True,
            allow_internal=True,
            allow_external=True,
            prefer_external=False,
        )
    except NoURLAvailableError:
        base = _configured_url_matching_request(hass)
    if not base:
        raise NoURLAvailableError
    return f"{base.rstrip('/')}{HANDOFF_CALLBACK_PATH}"


def _configured_url_matching_request(hass: HomeAssistant) -> str:
    """Return a configured URL only if it is the host this browser came in on.

    ``get_url`` refuses whenever the address the request arrived on is not one
    it recognises. Handing out a configured URL anyway would send the
    authorization page at an address this particular browser may not be able to
    reach — someone on the LAN while only an external URL is set, say — and the
    failure would surface much later, as a window that never comes back, with
    nothing to point at. Matching the host first keeps that from happening; the
    caller turns "no match" into a plain "we do not know our own address",
    which the user can act on.
    """
    request = current_request.get()
    if request is None or not (host := request.url.host):
        return ""
    for candidate in (hass.config.internal_url, hass.config.external_url):
        if candidate and URL(candidate).host == host:
            return candidate
    return ""


class YunkanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Yunkan config and reauth flows."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise transient connection and handoff state."""
        self._reauth_data: dict[str, Any] = {}
        self._base_url: str = ""
        self._verify_ssl: bool = DEFAULT_VERIFY_SSL
        self._handoff_supported: bool = False
        self._handoff_state: str | None = None
        self._handoff_code: str | None = None
        self._handoff_error: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "YunkanOptionsFlow":
        """Return the options flow (event snapshot rendering)."""
        return YunkanOptionsFlow()

    @callback
    def async_remove(self) -> None:
        """Forget a pending authorization when the flow goes away.

        Covers every ending — created, aborted, or abandoned by closing the
        dialog — so a stale state can never point at a flow that is gone.
        """
        async_forget_handoff_state(self.hass, self._handoff_state)

    # ------------------------------------------------------------ user setup

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the server address, then let the server pick the sign-in path."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._base_url = normalize_base_url(user_input[CONF_BASE_URL])
            self._verify_ssl = user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            if user_input.get(CONF_USE_PASSWORD):
                # Asked for the password form outright, so do not probe: the
                # probe is the first thing a browser-side problem breaks, and
                # the login on the next step reports an unreachable server or
                # one still in setup just as well.
                return await self.async_step_credentials()
            error = await self._async_probe_server()
            if error is None:
                if not self._handoff_supported:
                    return await self.async_step_credentials()
                try:
                    return self._async_start_handoff()
                except NoURLAvailableError:
                    error = "no_url"
            errors["base"] = error
        return self._async_show_user_form(errors, user_input)

    def _async_show_user_form(
        self, errors: dict[str, str], user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Render the address form, keeping whatever the user already typed."""
        current = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BASE_URL,
                    default=current.get(CONF_BASE_URL, self._base_url or DEFAULT_BASE_URL),
                ): str,
                vol.Optional(
                    CONF_VERIFY_SSL,
                    default=current.get(CONF_VERIFY_SSL, self._verify_ssl),
                ): bool,
                vol.Optional(CONF_USE_PASSWORD, default=False): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a username and password (server too old for the handoff)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            error = await self._async_try_login(username, password)
            if error is None:
                return await self._async_finish(
                    {
                        CONF_BASE_URL: self._base_url,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_VERIFY_SSL: self._verify_ssl,
                    }
                )
            errors["base"] = error
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME, default=(user_input or {}).get(CONF_USERNAME, "")
                ): str,
                vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="credentials",
            data_schema=schema,
            description_placeholders={CONF_BASE_URL: self._base_url},
            errors=errors,
        )

    # --------------------------------------------------------------- reauth

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start a reauth flow (the stored credential was rejected)."""
        self._reauth_data = dict(entry_data)
        self._base_url = self._reauth_data[CONF_BASE_URL]
        self._verify_ssl = self._reauth_data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm, then take the same fork as a fresh setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_USE_PASSWORD):
                # Same escape hatch as the address step (see async_step_user).
                return await self.async_step_reauth_password()
            error = await self._async_probe_server()
            if error is None:
                if not self._handoff_supported:
                    return await self.async_step_reauth_password()
                try:
                    return self._async_start_handoff()
                except NoURLAvailableError:
                    error = "no_url"
            errors["base"] = error
        return self._async_show_reauth_confirm(errors)

    def _async_show_reauth_confirm(self, errors: dict[str, str]) -> ConfigFlowResult:
        """Render the reauth confirmation (a button and the password opt-out)."""
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Optional(CONF_USE_PASSWORD, default=False): bool}
            ),
            description_placeholders={
                CONF_USERNAME: self._reauth_data.get(CONF_USERNAME, ""),
                CONF_BASE_URL: self._base_url,
            },
            errors=errors,
        )

    async def async_step_reauth_password(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a fresh password (server too old for the handoff)."""
        errors: dict[str, str] = {}
        username = self._reauth_data.get(CONF_USERNAME, "")
        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            error = await self._async_try_login(username, password)
            if error is None:
                return await self._async_finish(
                    {
                        CONF_BASE_URL: self._base_url,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_VERIFY_SSL: self._verify_ssl,
                    }
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_password",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR}),
            description_placeholders={
                CONF_USERNAME: username,
                CONF_BASE_URL: self._base_url,
            },
            errors=errors,
        )

    # -------------------------------------------------------------- handoff

    def _async_start_handoff(self) -> ConfigFlowResult:
        """Send the user to the server's authorization page.

        Raises :class:`NoURLAvailableError` when we cannot work out an address
        the user's browser could be redirected back to.
        """
        redirect_uri = _callback_redirect_uri(self.hass)
        # Registered here, not only in async_setup: setting up an integration
        # does not run async_setup before its config flow, so on a first-time
        # install the callback view would not exist yet.
        async_register_handoff_view(self.hass)
        async_forget_handoff_state(self.hass, self._handoff_state)
        self._handoff_state = secrets.token_urlsafe(32)
        async_register_handoff_state(self.hass, self._handoff_state, self.flow_id)
        query = urlencode(
            {
                "client": HANDOFF_CLIENT_NAME,
                "state": self._handoff_state,
                "redirect_uri": redirect_uri,
            },
            safe="",
            quote_via=quote,
        )
        return self.async_external_step(
            step_id="handoff",
            url=f"{self._base_url}{HANDOFF_AUTHORIZE_PATH}?{query}",
        )

    async def async_step_handoff(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take the authorization result the callback view handed us.

        An external step may only move to another external step or to "done",
        so both outcomes land in ``handoff_done``, which is where the error (if
        any) is turned into something the user sees.
        """
        if user_input and user_input.get("code"):
            self._handoff_code = user_input["code"]
            self._handoff_error = None
        else:
            self._handoff_code = None
            self._handoff_error = (user_input or {}).get("error") or "access_denied"
        return self.async_external_step_done(next_step_id="handoff_done")

    async def async_step_handoff_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Trade the one-time code for a long-lived API token."""
        state = self._handoff_state
        async_forget_handoff_state(self.hass, state)
        self._handoff_state = None
        code = self._handoff_code
        self._handoff_code = None

        if code is None or state is None:
            error = self._handoff_error or "handoff_failed"
            return self._async_handoff_error(
                error if error == "access_denied" else "handoff_failed"
            )

        session = async_get_clientsession(self.hass, verify_ssl=self._verify_ssl)
        client = YunkanApiClient(session, self._base_url)
        try:
            result = await client.async_handoff_exchange(code, state)
        except YunkanConnectionError:
            return self._async_handoff_error("cannot_connect")
        except YunkanApiError:
            return self._async_handoff_error("handoff_failed")
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error exchanging the Yunkan authorization code")
            return self._async_handoff_error("unknown")

        user = result.get("user") or {}
        return await self._async_finish(
            {
                CONF_BASE_URL: self._base_url,
                # Display only — the token, not this name, is the credential.
                CONF_USERNAME: user.get("username") or self._reauth_data.get(
                    CONF_USERNAME, ""
                ),
                CONF_API_TOKEN: result["token"],
                CONF_VERIFY_SSL: self._verify_ssl,
            }
        )

    def _async_handoff_error(self, error: str) -> ConfigFlowResult:
        """Return to the step the handoff was started from, showing an error."""
        if self.source == SOURCE_REAUTH:
            return self._async_show_reauth_confirm({"base": error})
        return self._async_show_user_form({"base": error})

    # --------------------------------------------------------------- shared

    async def _async_finish(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Create the entry, or update it in place when this was a reauth.

        ``data`` replaces the stored data wholesale rather than being merged,
        which is what drops the password from an entry that has just been
        upgraded to an API token (and the other way round).
        """
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data=data
            )
        await self.async_set_unique_id(URL(self._base_url).host or self._base_url)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=URL(self._base_url).host or "Yunkan", data=data
        )

    async def _async_probe_server(self) -> str | None:
        """Check the server is reachable and note how it wants to authenticate.

        Returns an error key, or ``None`` with ``_handoff_supported`` set.
        """
        session = async_get_clientsession(self.hass, verify_ssl=self._verify_ssl)
        client = YunkanApiClient(session, self._base_url)
        try:
            status = await client.async_setup_status()
            if status.get("needs_setup"):
                return "setup_required"
        except YunkanConnectionError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001 - older servers may not expose it
            pass
        try:
            self._handoff_supported = await client.async_handoff_supported()
        except YunkanConnectionError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001 - treat anything unexpected as "no"
            _LOGGER.debug("Handoff probe failed; falling back to a password", exc_info=True)
            self._handoff_supported = False
        return None

    async def _async_try_login(self, username: str, password: str) -> str | None:
        """Attempt a password login; return an error key or ``None`` on success."""
        session = async_get_clientsession(self.hass, verify_ssl=self._verify_ssl)
        client = YunkanApiClient(session, self._base_url, username, password)
        try:
            await client.async_login()
        except YunkanMfaRequiredError:
            # Subclass of YunkanAuthError, so it has to come first — and it is
            # worth its own message: the password was right, and telling this
            # user to check it would send them looking in the wrong place.
            return "mfa_required"
        except YunkanAuthError:
            return "invalid_auth"
        except YunkanSetupRequiredError:
            return "setup_required"
        except YunkanConnectionError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error validating Yunkan credentials")
            return "unknown"
        return None


class YunkanOptionsFlow(OptionsFlow):
    """Options: event snapshot rendering + the optional sidebar panel.

    The snapshot toggles map straight onto the backend snapshot endpoint
    parameters (``annotate`` / ``crop``); the integration itself does no image
    work. The sidebar panel embeds the Yunkan web console as an iframe panel
    (see panel.py for its reachability / HTTPS caveats). Saving reloads the
    entry (see ``_async_update_listener``), so both take effect immediately.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Single-step form with the snapshot toggles and panel options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    OPT_SNAPSHOT_BBOX,
                    default=options.get(OPT_SNAPSHOT_BBOX, DEFAULT_SNAPSHOT_BBOX),
                ): bool,
                vol.Optional(
                    OPT_SNAPSHOT_CROP,
                    default=options.get(OPT_SNAPSHOT_CROP, DEFAULT_SNAPSHOT_CROP),
                ): bool,
                vol.Optional(
                    OPT_SIDEBAR_PANEL,
                    default=options.get(OPT_SIDEBAR_PANEL, DEFAULT_SIDEBAR_PANEL),
                ): bool,
                vol.Optional(
                    OPT_PANEL_TITLE,
                    default=options.get(OPT_PANEL_TITLE, DEFAULT_PANEL_TITLE),
                ): str,
                # No default on purpose: an empty (cleared) field is simply not
                # submitted, and panel.py then falls back to the connection URL.
                vol.Optional(
                    OPT_PANEL_URL,
                    description={"suggested_value": options.get(OPT_PANEL_URL, "")},
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
