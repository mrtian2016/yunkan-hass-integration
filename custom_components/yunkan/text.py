"""Text platform for the Yunkan integration.

A per-camera **voice-broadcast message** field, paired with the Broadcast button
(``button.py``), so users can type a message and speak it on the camera's speaker
straight from the device page — handy for testing voice broadcast without wiring
up an automation. Only cameras that support talkback get one.
"""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YunkanConfigEntry
from .coordinator import YunkanCoordinator
from .entity import YunkanCameraEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YunkanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yunkan text entities (voice-broadcast message) from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        YunkanTtsMessageText(coordinator, camera_id)
        for camera_id, camera in coordinator.data.cameras.items()
        if camera.get("talkback_supported")
    )


class YunkanTtsMessageText(YunkanCameraEntity, TextEntity):
    """The draft message that the Broadcast button will speak."""

    _attr_translation_key = "tts_message"
    _attr_icon = "mdi:message-text"
    _attr_native_max = 200

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the broadcast-message text field."""
        super().__init__(coordinator, camera_id)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_tts_message"

    @property
    def native_value(self) -> str | None:
        """Return the current draft message (empty until the user types one)."""
        return self.coordinator.tts_messages.get(self._camera_id, "")

    async def async_set_value(self, value: str) -> None:
        """Store the draft message for the Broadcast button to read."""
        self.coordinator.tts_messages[self._camera_id] = value
        self.async_write_ha_state()
