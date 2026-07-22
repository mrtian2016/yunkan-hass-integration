"""The Yunkan integration.

A thin Home Assistant client for a self-hosted Yunkan camera server. All
gating, authentication and data live on the server; this integration maps the
public REST/SSE contract onto HA entities, a media browser and a few services.
"""

from __future__ import annotations

from importlib import import_module
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    YunkanApiClient,
    YunkanAuthError,
    YunkanConnectionError,
    YunkanSetupRequiredError,
)
from .const import CONF_BASE_URL, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL, DOMAIN
from .coordinator import YunkanCoordinator
from .entity import server_device_info
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


def _preimport_platforms() -> None:
    """Import platform modules eagerly (runs in the import executor)."""
    for platform in PLATFORMS:
        import_module(f"{__package__}.{platform.value}")


def _async_prune_stale_devices(
    hass: HomeAssistant, entry: YunkanConfigEntry, coordinator: YunkanCoordinator
) -> None:
    """Remove devices for cameras that are no longer enabled or present."""
    device_registry = dr.async_get(hass)
    valid = {f"{entry.entry_id}_{cid}" for cid in coordinator.data.cameras}
    valid.add(entry.entry_id)  # the server hub device
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        identifiers = {ident for domain, ident in device.identifiers if domain == DOMAIN}
        if identifiers and not (identifiers & valid):
            device_registry.async_remove_device(device.id)


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

    # Register the server (hub) device so per-camera devices can reference it as
    # their via_device.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        **server_device_info(entry.entry_id, client.base_url, coordinator.data.version),
    )

    # Register the HA-authenticated proxy views and services once, globally.
    async_register_views(hass)
    async_setup_services(hass)

    # Import platform modules off the event loop so the forwarded setup below is
    # a cache hit and never triggers a blocking import inside the loop.
    await hass.async_add_import_executor_job(_preimport_platforms)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _async_prune_stale_devices(hass, entry, coordinator)

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
