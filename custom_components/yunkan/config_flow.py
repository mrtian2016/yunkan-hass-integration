"""Config flow for the Yunkan integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol
from yarl import URL

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    YunkanApiClient,
    YunkanAuthError,
    YunkanConnectionError,
    YunkanSetupRequiredError,
)
from .const import (
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_BASE_URL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _normalize_base_url(raw: str) -> str:
    """Normalise a user-entered base URL (add scheme, strip trailing slash)."""
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    return raw.rstrip("/")


class YunkanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Yunkan config and reauth flows."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise transient reauth state."""
        self._reauth_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial connection + login step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = _normalize_base_url(user_input[CONF_BASE_URL])
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
