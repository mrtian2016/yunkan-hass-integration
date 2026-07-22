"""Switch platform for the Yunkan integration.

A single switch per camera toggles AI detection. Detection is a Pro feature, so
on a free-tier server the toggle raises a clear error and surfaces a repair
issue rather than silently failing. The current state is always readable because
it rides on the (free-tier) camera object.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YunkanConfigEntry
from .api import YunkanApiError, YunkanProRequiredError
from .coordinator import YunkanCoordinator
from .entity import YunkanCameraEntity
from .issues import async_raise_pro_required

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YunkanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yunkan detection switches from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        YunkanDetectionSwitch(coordinator, camera_id)
        for camera_id in coordinator.data.cameras
    )


class YunkanDetectionSwitch(YunkanCameraEntity, SwitchEntity):
    """Toggle AI detection for a camera."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_translation_key = "detection"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:motion-sensor"

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the detection switch."""
        super().__init__(coordinator, camera_id)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_detection"

    @property
    def is_on(self) -> bool:
        """Return whether detection is enabled for the camera."""
        return bool(self._camera.get("detection_enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable detection."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable detection."""
        await self._async_set(False)

    async def _async_set(self, desired: bool) -> None:
        """Toggle detection only if the desired state differs from current."""
        if bool(self._camera.get("detection_enabled")) == desired:
            return
        try:
            result = await self.coordinator.client.async_toggle_detection(self._camera_id)
        except YunkanProRequiredError as err:
            async_raise_pro_required(self.hass, err.license_status)
            raise HomeAssistantError(
                "AI detection is a Pro feature; the server is on the free tier."
            ) from err
        except YunkanApiError as err:
            raise HomeAssistantError(f"Failed to toggle detection: {err}") from err
        # Reflect the authoritative new value immediately, then refresh.
        if isinstance(result, dict) and self._camera_id in self.coordinator.data.cameras:
            self.coordinator.data.cameras[self._camera_id]["detection_enabled"] = bool(
                result.get("detection_enabled", desired)
            )
            self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
