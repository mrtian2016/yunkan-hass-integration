"""Sensor platform for the Yunkan integration.

Deliberately minimal (fps and internal metrics stay in Web Admin): one
"last event" timestamp sensor per camera, updated from the SSE stream, with the
event category, confidence and summary carried as attributes.
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import YunkanConfigEntry
from .const import EVENT_CATEGORY_MAP
from .coordinator import YunkanCoordinator, signal_event
from .entity import YunkanCameraEntity
from .event_utils import event_attributes

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YunkanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yunkan sensors from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        YunkanLastEventSensor(coordinator, camera_id)
        for camera_id in coordinator.data.cameras
    )


def _parse_event_time(value: Any) -> datetime | None:
    """Parse a naive local backend timestamp into an aware datetime."""
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return parsed


class YunkanLastEventSensor(YunkanCameraEntity, SensorEntity):
    """Timestamp of the most recent detection event for a camera."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "last_event"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the last-event sensor."""
        super().__init__(coordinator, camera_id)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_last_event"
        self._attr_native_value: datetime | None = None
        self._attr_extra_state_attributes: dict[str, Any] = {}
        self._apply_event(coordinator.latest_events.get(camera_id))

    async def async_added_to_hass(self) -> None:
        """Subscribe to the real-time event signal."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_event(self._entry_id), self._handle_event
            )
        )

    @callback
    def _handle_event(self, event: dict[str, Any]) -> None:
        """Update the sensor when an event fires for this camera."""
        if event.get("camera_id") != self._camera_id:
            return
        self._apply_event(event)
        self.async_write_ha_state()

    def _apply_event(self, event: dict[str, Any] | None) -> None:
        """Store the given event as the sensor's state."""
        if not event:
            return
        when = _parse_event_time(event.get("event_time"))
        if when is not None:
            self._attr_native_value = when
        event_type = event.get("event_type", "")
        attrs = event_attributes(event)
        attrs["category"] = EVENT_CATEGORY_MAP.get(event_type)
        self._attr_extra_state_attributes = attrs
