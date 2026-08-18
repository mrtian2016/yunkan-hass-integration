"""Config flow for the Yunkan integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol
from yarl import URL

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    YunkanApiClient,
    YunkanAuthError,
    YunkanConnectionError,
    YunkanSetupRequiredError,
    normalize_base_url,
)
from .const import (
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_BASE_URL,
    DEFAULT_PANEL_TITLE,
    DEFAULT_SIDEBAR_PANEL,
    DEFAULT_SNAPSHOT_BBOX,
    DEFAULT_SNAPSHOT_CROP,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    OPT_PANEL_TITLE,
    OPT_PANEL_URL,
    OPT_SIDEBAR_PANEL,
    OPT_SNAPSHOT_BBOX,
    OPT_SNAPSHOT_CROP,
)

_LOGGER = logging.getLogger(__name__)


class YunkanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Yunkan config and reauth flows."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise transient reauth state."""
        self._reauth_data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "YunkanOptionsFlow":
        """Return the options flow (event snapshot rendering)."""
        return YunkanOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial connection + login step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = normalize_base_url(user_input[CONF_BASE_URL])
            error = await self._async_try_login(
                base_url,
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            )
            if error is None:
                await self.async_set_unique_id(URL(base_url).host or base_url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=URL(base_url).host or "Yunkan",
                    data={
                        CONF_BASE_URL: base_url,
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                    },
                )
            errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BASE_URL,
                    default=(user_input or {}).get(CONF_BASE_URL, DEFAULT_BASE_URL),
                ): str,
                vol.Required(
                    CONF_USERNAME, default=(user_input or {}).get(CONF_USERNAME, "")
                ): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start a reauth flow (credentials rejected)."""
        self._reauth_data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a fresh password and revalidate."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            error = await self._async_try_login(
                self._reauth_data[CONF_BASE_URL],
                self._reauth_data[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                self._reauth_data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data={**self._reauth_data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={
                CONF_USERNAME: self._reauth_data.get(CONF_USERNAME, ""),
                CONF_BASE_URL: self._reauth_data.get(CONF_BASE_URL, ""),
            },
            errors=errors,
        )

    async def _async_try_login(
        self, base_url: str, username: str, password: str, verify_ssl: bool
    ) -> str | None:
        """Attempt a login; return an error key or ``None`` on success."""
        session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
        client = YunkanApiClient(session, base_url, username, password)
        try:
            status = await client.async_setup_status()
            if status.get("needs_setup"):
                return "setup_required"
        except YunkanConnectionError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001 - fall through to the login attempt
            pass
        try:
            await client.async_login()
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
