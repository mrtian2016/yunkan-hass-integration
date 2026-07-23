"""Button platform for the Yunkan integration.

One-tap actions that show on the camera device page / dashboard, so users can
invoke them directly instead of only from Developer Tools → Actions:

* a **Refresh snapshot** button per camera (forces a fresh grab);
* for PTZ cameras: **up / down / left / right** buttons that start a *continuous*
  move and a **Stop** button that ends it. A safety timer stops the move if Stop
  is never pressed (the backend ContinuousMove has no timeout). Presets are a
  dropdown ``select`` entity instead (see ``select.py``).

Actions that need free-form input (voice broadcast text, export time range) can't
be one-tap buttons — those stay as services / device actions.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YunkanConfigEntry
from .api import YunkanApiError, YunkanProRequiredError
from .coordinator import YunkanCoordinator
from .entity import YunkanCameraEntity
from .issues import async_raise_pro_required

_LOGGER = logging.getLogger(__name__)

# A direction button starts a continuous move; Stop (or the safety timer) ends
# it. The safety window is generous enough to pan across the scene in one press
# yet bounded so a missed Stop can't run the camera to its limit forever.
_PTZ_MOVE_SPEED = 0.5
_PTZ_MAX_MOVE_SEC = 8.0

_PTZ_DIRECTIONS: dict[str, str] = {
    "up": "mdi:arrow-up-bold",
    "down": "mdi:arrow-down-bold",
    "left": "mdi:arrow-left-bold",
    "right": "mdi:arrow-right-bold",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YunkanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yunkan buttons from a config entry."""
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = []
    for camera_id, camera in coordinator.data.cameras.items():
        entities.append(YunkanSnapshotButton(coordinator, camera_id))
        if camera.get("talkback_supported"):
            entities.append(YunkanTtsBroadcastButton(coordinator, camera_id))
        if not camera.get("has_ptz"):
            continue
        entities.extend(
            YunkanPtzDirectionButton(coordinator, camera_id, direction)
            for direction in _PTZ_DIRECTIONS
        )
        entities.append(YunkanPtzStopButton(coordinator, camera_id))
    async_add_entities(entities)


class YunkanSnapshotButton(YunkanCameraEntity, ButtonEntity):
    """Force a fresh snapshot grab for a camera."""

    _attr_translation_key = "refresh_snapshot"
    _attr_icon = "mdi:camera-iris"

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the refresh-snapshot button."""
        super().__init__(coordinator, camera_id)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_refresh_snapshot"

    async def async_press(self) -> None:
        """Force the backend to capture a fresh frame."""
        try:
            image = await self.coordinator.client.async_snapshot(
                self._camera_id, force=True
            )
        except YunkanApiError as err:
            raise HomeAssistantError(f"Snapshot refresh failed: {err}") from err
        # async_snapshot returns None on a failed/never-captured grab (e.g. camera
        # offline) rather than raising — surface that instead of a silent success.
        if image is None:
            raise HomeAssistantError(
                "Snapshot refresh failed: no frame available (camera offline?)"
            )


class YunkanTtsBroadcastButton(YunkanCameraEntity, ButtonEntity):
    """Speak the drafted message (from the message text field) on the speaker."""

    _attr_translation_key = "tts_broadcast"
    _attr_icon = "mdi:bullhorn"

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the voice-broadcast button."""
        super().__init__(coordinator, camera_id)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_tts_broadcast"

    async def async_press(self) -> None:
        """Broadcast the drafted message; Pro-gated, admin only."""
        message = (self.coordinator.tts_messages.get(self._camera_id) or "").strip()
        if not message:
            raise HomeAssistantError(
                "Type a message in the broadcast text field first."
            )
        try:
            await self.coordinator.client.async_tts_broadcast(self._camera_id, message)
        except YunkanProRequiredError as err:
            async_raise_pro_required(self.hass, err.license_status)
            raise HomeAssistantError(
                "Voice broadcast is a Pro feature; the server is on the free tier."
            ) from err
        except YunkanApiError as err:
            raise HomeAssistantError(f"Voice broadcast failed: {err}") from err


class YunkanPtzDirectionButton(YunkanCameraEntity, ButtonEntity):
    """Start a continuous PTZ move in one direction (press Stop to end it)."""

    def __init__(
        self, coordinator: YunkanCoordinator, camera_id: str, direction: str
    ) -> None:
        """Initialise a PTZ direction button."""
        super().__init__(coordinator, camera_id)
        self._direction = direction
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_ptz_{direction}"
        self._attr_translation_key = f"ptz_{direction}"
        self._attr_icon = _PTZ_DIRECTIONS[direction]

    async def async_press(self) -> None:
        """Start moving; a safety timer stops it if Stop is never pressed."""
        # Arm the safety stop *first*: if the move reaches the camera but its
        # response is lost, the timer still guarantees a stop. A stop for a move
        # that never actually started is a harmless no-op.
        self.coordinator.schedule_ptz_safety_stop(self._camera_id, _PTZ_MAX_MOVE_SEC)
        try:
            await self.coordinator.client.async_ptz(
                self._camera_id, self._direction, _PTZ_MOVE_SPEED
            )
        except YunkanApiError as err:
            raise HomeAssistantError(f"PTZ move failed: {err}") from err


class YunkanPtzStopButton(YunkanCameraEntity, ButtonEntity):
    """Stop PTZ movement on a camera."""

    _attr_translation_key = "ptz_stop"
    _attr_icon = "mdi:stop"

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the PTZ stop button."""
        super().__init__(coordinator, camera_id)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_ptz_stop"

    async def async_press(self) -> None:
        """Stop any active PTZ movement and cancel the pending safety stop."""
        try:
            await self.coordinator.client.async_ptz_stop(self._camera_id)
        except YunkanApiError as err:
            raise HomeAssistantError(f"PTZ stop failed: {err}") from err
        # Cancel only after a confirmed stop, so a failed stop keeps the safety
        # timer as the fallback.
        self.coordinator.cancel_ptz_safety_stop(self._camera_id)
