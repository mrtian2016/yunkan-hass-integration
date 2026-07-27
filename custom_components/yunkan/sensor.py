"""Sensor platform for the Yunkan integration.

Internal metrics (fps, cpu, gpu) stay in Web Admin. Per camera this exposes a
"last event" timestamp, a live object-count per category, and the last
recognised face, licence plate and gesture — all fed by the event / live-tracks
streams.

The count and recognition sensors exist only while the detection feature behind
them is enabled (see feature_gate); "last event" is always present.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import YunkanConfigEntry
from .const import (
    CATEGORY_FEATURE,
    CATEGORY_META,
    EVENT_CATEGORY_MAP,
    OBJECT_COUNT_CATEGORIES,
)
from .coordinator import YunkanCoordinator, signal_event, signal_tracks
from .entity import YunkanCameraEntity
from .event_utils import event_attributes, parse_extra
from .feature_gate import FeatureEntity, async_setup_feature_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YunkanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yunkan sensors from a config entry."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    specs: list[FeatureEntity] = []
    # Recognition sensors, keyed by the feature that produces the value.
    recognition = (
        ("face", YunkanRecognizedFaceSensor),
        ("plate", YunkanRecognizedPlateSensor),
        ("gesture", YunkanRecognizedGestureSensor),
    )
    for camera_id in coordinator.data.cameras:
        entities.append(YunkanLastEventSensor(coordinator, camera_id))
        specs.extend(
            FeatureEntity(
                camera_id=camera_id,
                feature=feature,
                key=f"recognized_{feature}",
                factory=partial(cls, coordinator, camera_id),
            )
            for feature, cls in recognition
        )
        specs.extend(
            FeatureEntity(
                camera_id=camera_id,
                feature=CATEGORY_FEATURE[category],
                key=f"{category}_count",
                factory=partial(
                    YunkanObjectCountSensor, coordinator, camera_id, category
                ),
            )
            for category in OBJECT_COUNT_CATEGORIES
        )
    async_add_entities(entities)
    async_setup_feature_entities(
        hass, entry, coordinator, async_add_entities, "sensor", specs
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


class YunkanObjectCountSensor(YunkanCameraEntity, SensorEntity):
    """Live count of a detected object category for a camera."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "objects"

    def __init__(
        self, coordinator: YunkanCoordinator, camera_id: str, category: str
    ) -> None:
        """Initialise the object-count sensor."""
        super().__init__(coordinator, camera_id)
        self._category = category
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_{category}_count"
        self._attr_translation_key = f"{category}_count"
        self._attr_icon = CATEGORY_META[category][2]

    @property
    def native_value(self) -> int:
        """Return the current count of this category."""
        return self.coordinator.tracks_counts.get(self._camera_id, {}).get(self._category, 0)

    async def async_added_to_hass(self) -> None:
        """Subscribe to the live occupancy signal."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_tracks(self._entry_id), self._handle_tracks
            )
        )

    @callback
    def _handle_tracks(self, camera_id: str) -> None:
        """Update the count when live tracks change for this camera."""
        if camera_id == self._camera_id:
            self.async_write_ha_state()


class _YunkanRecognitionSensor(YunkanCameraEntity, SensorEntity):
    """Base sensor holding the last recognised value (face name / plate)."""

    _extra_key: str

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the recognition sensor."""
        super().__init__(coordinator, camera_id)
        self._attr_native_value: str | None = None
        self._attr_extra_state_attributes: dict[str, Any] = {}

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
        """Update when an event carries a recognised value for this camera."""
        if event.get("camera_id") != self._camera_id:
            return
        value = parse_extra(event).get(self._extra_key)
        if not value:
            return
        self._attr_native_value = str(value)
        self._attr_extra_state_attributes = {
            "event_id": event.get("id"),
            "event_time": event.get("event_time"),
            "confidence": event.get("confidence"),
        }
        self.async_write_ha_state()


class YunkanRecognizedFaceSensor(_YunkanRecognitionSensor):
    """The most recently recognised face name on a camera."""

    _attr_translation_key = "recognized_face"
    _attr_icon = "mdi:face-recognition"
    _extra_key = "name"

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the recognised-face sensor."""
        super().__init__(coordinator, camera_id)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_recognized_face"


class YunkanRecognizedPlateSensor(_YunkanRecognitionSensor):
    """The most recently recognised licence plate on a camera."""

    _attr_translation_key = "recognized_plate"
    _attr_icon = "mdi:car-info"
    _extra_key = "plate"

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the recognised-plate sensor."""
        super().__init__(coordinator, camera_id)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_recognized_plate"


class YunkanRecognizedGestureSensor(_YunkanRecognitionSensor):
    """The most recently recognised gesture label on a camera."""

    _attr_translation_key = "recognized_gesture"
    _attr_icon = "mdi:hand-wave"
    _extra_key = "gesture"

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the recognised-gesture sensor."""
        super().__init__(coordinator, camera_id)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_recognized_gesture"
