"""Image platform for the Yunkan integration.

A "latest event" image per camera plus one per detection category. The SSE
stream tells us when a new event snapshot exists; the JPEG itself (with
detection boxes drawn server-side) is fetched on demand and cached by the base
ImageEntity.

Per-category images exist only while the detection feature behind them is
enabled (see feature_gate); the "latest event" image is always present.
"""

from __future__ import annotations

from functools import partial
import logging
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import YunkanConfigEntry
from .const import (
    CATEGORY_FEATURE,
    DEFAULT_SNAPSHOT_BBOX,
    DEFAULT_SNAPSHOT_CROP,
    EVENT_CATEGORIES,
    EVENT_CATEGORY_MAP,
    OPT_SNAPSHOT_BBOX,
    OPT_SNAPSHOT_CROP,
)
from .coordinator import YunkanCoordinator, signal_event
from .entity import YunkanCameraEntity
from .feature_gate import FeatureEntity, async_setup_feature_entities

_LOGGER = logging.getLogger(__name__)

_THUMBNAIL_WIDTH = 1280


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YunkanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yunkan event images (latest + per category) from a config entry."""
    coordinator = entry.runtime_data
    entities: list[ImageEntity] = []
    specs: list[FeatureEntity] = []
    for camera_id in coordinator.data.cameras:
        entities.append(YunkanLatestEventImage(hass, coordinator, camera_id))
        specs.extend(
            FeatureEntity(
                camera_id=camera_id,
                feature=CATEGORY_FEATURE[category],
                key=category,
                factory=partial(
                    YunkanLatestEventImage, hass, coordinator, camera_id, category
                ),
            )
            for category in EVENT_CATEGORIES
        )
    async_add_entities(entities)
    async_setup_feature_entities(
        hass, entry, coordinator, async_add_entities, "image", specs
    )


class YunkanLatestEventImage(YunkanCameraEntity, ImageEntity):
    """The most recent event snapshot for a camera, optionally for one category."""

    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: YunkanCoordinator,
        camera_id: str,
        category: str | None = None,
    ) -> None:
        """Initialise the image entity (category=None → any event)."""
        YunkanCameraEntity.__init__(self, coordinator, camera_id)
        ImageEntity.__init__(self, hass)
        self._category = category
        if category is None:
            self._attr_unique_id = f"{self._entry_id}_{camera_id}_latest_event"
            self._attr_translation_key = "latest_event"
        else:
            self._attr_unique_id = f"{self._entry_id}_{camera_id}_latest_{category}"
            self._attr_translation_key = f"latest_{category}"
        self._snapshot_url: str | None = None
        self._event_id: int | None = None
        if category is None:
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
        """Refresh the image when a matching snapshot arrives for this camera."""
        if event.get("camera_id") != self._camera_id:
            return
        if not event.get("snapshot_url"):
            return
        if self._category is not None and (
            EVENT_CATEGORY_MAP.get(event.get("event_type", "")) != self._category
        ):
            return
        self._apply_event(event)
        self._attr_image_last_updated = dt_util.utcnow()
        self._cached_image = None
        self.async_write_ha_state()

    def _apply_event(self, event: dict[str, Any] | None) -> None:
        """Store the snapshot pointer from an event."""
        if not event or not event.get("snapshot_url"):
            return
        self._snapshot_url = event["snapshot_url"]
        self._event_id = event.get("id")
        if self._attr_image_last_updated is None:
            self._attr_image_last_updated = dt_util.utcnow()

    async def async_image(self) -> bytes | None:
        """Return the latest event snapshot JPEG, rendered per entry options.

        Bounding boxes (default on) and object crop (default off) are applied
        server-side; both need the event id. Options changes reload the entry,
        so reading them per fetch is always current.
        """
        if not self._snapshot_url:
            return None
        options = self.coordinator.config_entry.options
        draw_box = options.get(OPT_SNAPSHOT_BBOX, DEFAULT_SNAPSHOT_BBOX)
        crop = options.get(OPT_SNAPSHOT_CROP, DEFAULT_SNAPSHOT_CROP)
        return await self.coordinator.client.async_event_snapshot(
            self._snapshot_url,
            width=_THUMBNAIL_WIDTH,
            annotate_event_id=self._event_id if draw_box else None,
            crop_event_id=self._event_id if crop else None,
        )
