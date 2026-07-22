"""The Yunkan integration.

A thin Home Assistant client for a self-hosted Yunkan camera server. All
gating, authentication and data live on the server; this integration maps the
public REST/SSE contract onto HA entities, a media browser and a few services.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    YunkanApiClient,
    YunkanAuthError,
    YunkanConnectionError,
    YunkanSetupRequiredError,
)
from .const import CONF_BASE_URL, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from .coordinator import YunkanCoordinator
from .services import async_setup_services
from .views import async_register_views

_LOGGER = logging.getLogger(__name__)

type YunkanConfigEntry = ConfigEntry[YunkanCoordinator]

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.IMAGE,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


async def async_setup_entry(hass: HomeAssistant, entry: YunkanConfigEntry) -> bool:
    """Set up Yunkan from a config entry."""
    session = async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, False))
    client = YunkanApiClient(
        session,
        entry.data[CONF_BASE_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )

    try:
        await client.async_login()
    except YunkanAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except YunkanSetupRequiredError as err:
        raise ConfigEntryNotReady("Yunkan server is still in setup mode") from err
    except YunkanConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = YunkanCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    # Register the HA-authenticated proxy views and services once, globally.
    async_register_views(hass)
    async_setup_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start the real-time event stream after platforms are ready to receive it.
    await coordinator.async_start_stream()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: YunkanConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = entry.runtime_data
    await coordinator.async_stop_stream()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: YunkanConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
