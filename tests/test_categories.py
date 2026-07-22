"""Tests for the event-category mapping tables (no Home Assistant needed)."""

from __future__ import annotations

from custom_components.yunkan.const import (
    CATEGORY_META,
    EVENT_CATEGORIES,
    EVENT_CATEGORY_MAP,
)


def test_every_mapped_category_has_metadata() -> None:
    """Every category produced by the map must have display metadata."""
    for category in set(EVENT_CATEGORY_MAP.values()):
        assert category in CATEGORY_META


def test_event_categories_match_meta() -> None:
    """The ordered category tuple mirrors CATEGORY_META keys."""
    assert set(EVENT_CATEGORIES) == set(CATEGORY_META)


def test_known_labels_map_to_expected_categories() -> None:
    """Spot-check a few label -> category assignments against the server table."""
    assert EVENT_CATEGORY_MAP["person"] == "person"
    assert EVENT_CATEGORY_MAP["truck"] == "vehicle"
    assert EVENT_CATEGORY_MAP["dog"] == "animal"
    assert EVENT_CATEGORY_MAP["cry"] == "cry"


def test_unmapped_labels_are_absent() -> None:
    """Fine-grained labels that must not become sensors are not in the map."""
    for label in ("gesture", "plate", "motion", "zone_enter", "weather"):
        assert label not in EVENT_CATEGORY_MAP


def test_package_arrival_maps_to_package() -> None:
    """The real package event types drive the package sensor."""
    assert EVENT_CATEGORY_MAP["package_arrival"] == "package"
    assert EVENT_CATEGORY_MAP["package_removal"] == "package"


def test_device_classes_are_valid() -> None:
    """The device_class strings are the HA binary_sensor classes we expect."""
    valid = {"motion", "occupancy", "safety", "sound"}
    for _label, device_class, _icon in CATEGORY_META.values():
        assert device_class in valid
