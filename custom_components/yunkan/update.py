"""Update platform for the Yunkan integration.

An informational update entity that reflects the server's OTA version state.
Installing is intentionally left to Web Admin / the server's own OTA flow, so
this entity reports availability without performing the upgrade.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.update import UpdateEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import YunkanConfigEntry
from .coordinator import YunkanCoordinator
from .entity import server_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YunkanConfigEntry,
    async_add_entities,
) -> None:
    """Set up the Yunkan server update entity."""
    async_add_entities([YunkanServerUpdate(entry.runtime_data)])


class YunkanServerUpdate(CoordinatorEntity[YunkanCoordinator], UpdateEntity):
    """Report the Yunkan server version and available updates."""

    _attr_has_entity_name = True
    _attr_translation_key = "server"

    def __init__(self, coordinator: YunkanCoordinator) -> None:
        """Initialise the update entity bound to the server device."""
        super().__init__(coordinator)
        self._entry_id = coordinator.entry.entry_id
        self._attr_unique_id = f"{self._entry_id}_update"

    @property
    def device_info(self):
        """Return the server (hub) device info."""
        return server_device_info(
            self._entry_id, self.coordinator.client.base_url, self.coordinator.data.version
        )

    def _backend(self) -> dict[str, Any]:
        """Return the ``latest.backend`` block, if any."""
        latest = self.coordinator.data.version.get("latest")
        if isinstance(latest, dict):
            backend = latest.get("backend")
            if isinstance(backend, dict):
                return backend
        return {}

    @property
    def installed_version(self) -> str | None:
        """Return the currently installed backend version."""
        current = self.coordinator.data.version.get("current")
        if isinstance(current, dict):
            return current.get("backend")
        return None

    @property
    def latest_version(self) -> str | None:
        """Return the latest available backend version."""
        backend = self._backend()
        latest = backend.get("latest_version")
        if latest:
            return latest
        # No newer build reported: latest == installed.
        return self.installed_version

    @property
    def release_url(self) -> str | None:
        """Link to the changelog."""
        return "https://yun-kan.com/changelog"
