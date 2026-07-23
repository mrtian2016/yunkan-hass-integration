"""Binary sensor platform for the Yunkan integration.

Per camera, one occupancy-style sensor per detection category (person, vehicle,
animal, package, face, fall, baby-cry) plus a connectivity sensor. The category
sensors are driven by the real-time SSE stream: detection events are momentary,
so each sensor latches ON on a matching event and auto-resets after a short
delay, mirroring the server's MQTT-discovery behaviour.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import YunkanConfigEntry
from .const import (
    CATEGORY_META,
    EVENT_CATEGORIES,
    EVENT_CATEGORY_MAP,
    EVENT_OFF_DELAY,
)
from .coordinator import YunkanCoordinator, signal_event, signal_tracks
from .event_utils import event_attributes
from .entity import YunkanCameraEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YunkanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yunkan binary sensors from a config entry."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for camera_id in coordinator.data.cameras:
        entities.append(YunkanOnlineSensor(coordinator, camera_id))
        entities.append(YunkanMotionSensor(coordinator, camera_id))
        entities.extend(
            YunkanEventSensor(coordinator, camera_id, category)
            for category in EVENT_CATEGORIES
        )
    async_add_entities(entities)


class YunkanOnlineSensor(YunkanCameraEntity, BinarySensorEntity):
    """Connectivity sensor reflecting the camera's online state."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "online"
    _attr_entity_category = None

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the online sensor."""
        super().__init__(coordinator, camera_id)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_online"

    @property
    def is_on(self) -> bool:
        """Return True when the camera is online."""
        return bool(self._camera.get("is_online"))

    @property
    def available(self) -> bool:
        """Available whenever the poll succeeds and the camera exists."""
        return self.coordinator.last_update_success and bool(self._camera)


class YunkanMotionSensor(YunkanCameraEntity, BinarySensorEntity):
    """Generic motion sensor — ON while anything is tracked or a motion event fires."""

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_translation_key = "motion"

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the motion sensor."""
        super().__init__(coordinator, camera_id)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_motion"
        self._event_until: float = 0.0
        self._cancel_off: CALLBACK_TYPE | None = None

    @property
    def is_on(self) -> bool:
        """ON while any object is tracked or a recent motion event holds it."""
        if any(self.coordinator.tracks_counts.get(self._camera_id, {}).values()):
            return True
        return self._event_until > time.monotonic()

    async def async_added_to_hass(self) -> None:
        """Subscribe to the event and occupancy signals."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_event(self._entry_id), self._handle_event
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_tracks(self._entry_id), self._handle_tracks
            )
        )
        self.async_on_remove(self._cancel_off_timer)

    @callback
    def _handle_event(self, event: dict[str, Any]) -> None:
        """Hold ON on any detection event for this camera."""
        if event.get("camera_id") != self._camera_id:
            return
        if not event.get("event_type"):
            return
        self._event_until = time.monotonic() + EVENT_OFF_DELAY
        self._cancel_off_timer()
        self._cancel_off = async_call_later(self.hass, EVENT_OFF_DELAY, self._reeval)
        self.async_write_ha_state()

    @callback
    def _handle_tracks(self, camera_id: str) -> None:
        """React to a live occupancy change for this camera."""
        if camera_id == self._camera_id:
            self.async_write_ha_state()

    @callback
    def _reeval(self, _now: Any) -> None:
        """Re-evaluate state when the event hold expires."""
        self._cancel_off = None
        self.async_write_ha_state()

    @callback
    def _cancel_off_timer(self) -> None:
        """Cancel a pending re-evaluation timer."""
        if self._cancel_off is not None:
            self._cancel_off()
            self._cancel_off = None


class YunkanEventSensor(YunkanCameraEntity, BinarySensorEntity):
    """Occupancy sensor for one detection category.

    Turns ON immediately on a matching event (carrying rich attributes such as
    the recognised name or plate) and stays ON while live tracks show the
    category present. Categories without a live track (baby-cry, package) fall
    back to a momentary pulse that auto-clears after a short delay.
    """

    def __init__(
        self, coordinator: YunkanCoordinator, camera_id: str, category: str
    ) -> None:
        """Initialise the category occupancy sensor."""
        super().__init__(coordinator, camera_id)
        self._category = category
        label, device_class, icon = CATEGORY_META[category]
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_{category}"
        self._attr_translation_key = category
        self._attr_device_class = BinarySensorDeviceClass(device_class)
        self._attr_icon = icon
        self._attr_extra_state_attributes: dict[str, Any] = {}
        self._event_until: float = 0.0
        self._cancel_off: CALLBACK_TYPE | None = None

    @property
    def is_on(self) -> bool:
        """ON while the category is present or a recent event still holds it."""
        counts = self.coordinator.tracks_counts.get(self._camera_id, {})
        if counts.get(self._category, 0) > 0:
            return True
        return self._event_until > time.monotonic()

    async def async_added_to_hass(self) -> None:
        """Subscribe to the real-time event and occupancy signals."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_event(self._entry_id), self._handle_event
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_tracks(self._entry_id), self._handle_tracks
            )
        )
        self.async_on_remove(self._cancel_off_timer)

    @callback
    def _handle_event(self, event: dict[str, Any]) -> None:
        """Latch ON when an event of this category fires for this camera."""
        if event.get("camera_id") != self._camera_id:
            return
        if EVENT_CATEGORY_MAP.get(event.get("event_type", "")) != self._category:
            return
        self._attr_extra_state_attributes = event_attributes(event)
        self._event_until = time.monotonic() + EVENT_OFF_DELAY
        self._cancel_off_timer()
        self._cancel_off = async_call_later(self.hass, EVENT_OFF_DELAY, self._reeval)
        self.async_write_ha_state()

    @callback
    def _handle_tracks(self, camera_id: str) -> None:
        """React to a live occupancy change for this camera."""
        if camera_id == self._camera_id:
            self.async_write_ha_state()

    @callback
    def _reeval(self, _now: Any) -> None:
        """Re-evaluate state when the event hold expires."""
        self._cancel_off = None
        self.async_write_ha_state()

    @callback
    def _cancel_off_timer(self) -> None:
        """Cancel a pending re-evaluation timer."""
        if self._cancel_off is not None:
            self._cancel_off()
            self._cancel_off = None
