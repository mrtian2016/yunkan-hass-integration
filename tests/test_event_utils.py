"""Tests for event attribute extraction (no Home Assistant needed)."""

from __future__ import annotations

from custom_components.yunkan.event_utils import event_attributes, parse_extra


def test_parse_extra_from_json_string() -> None:
    """extra arrives as a JSON string and is parsed to a dict."""
    assert parse_extra({"extra": '{"a": 1}'}) == {"a": 1}
    assert parse_extra({"extra": None}) == {}
    assert parse_extra({"extra": "not json"}) == {}
    assert parse_extra({"extra": {"a": 2}}) == {"a": 2}


def test_face_event_exposes_name_and_similarity() -> None:
    """A face event surfaces the recognised name and match similarity."""
    event = {
        "id": 14732,
        "event_type": "face",
        "extra": (
            '{"detections": [{"label": "person", "score": 0.9},'
            ' {"label": "face", "score": 0.61, "name": "田继业"}],'
            ' "name": "田继业", "similarity": 0.616}'
        ),
    }
    attrs = event_attributes(event)
    assert attrs["name"] == "田继业"
    assert attrs["similarity"] == 0.616
    assert "face" in attrs["objects"]


def test_vehicle_event_exposes_plate() -> None:
    """A vehicle event folds the plate into attributes."""
    event = {
        "id": 5,
        "event_type": "car",
        "extra": (
            '{"detections": [{"label": "vehicle"}, {"label": "plate",'
            ' "plate": "沪A12345"}], "plate": "沪A12345",'
            ' "region": "CN", "color": "blue"}'
        ),
    }
    attrs = event_attributes(event)
    assert attrs["plate"] == "沪A12345"
    assert attrs["plate_region"] == "CN"
    assert attrs["plate_color"] == "blue"


def test_plain_event_has_no_name_or_plate() -> None:
    """A person event with no recognition carries no name/plate keys."""
    event = {"id": 1, "event_type": "person", "confidence": 0.8, "extra": "{}"}
    attrs = event_attributes(event)
    assert "name" not in attrs
    assert "plate" not in attrs
    assert attrs["confidence"] == 0.8
