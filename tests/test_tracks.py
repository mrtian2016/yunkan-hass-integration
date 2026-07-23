"""Tests for live-tracks occupancy/count derivation."""

from __future__ import annotations

from custom_components.yunkan.tracks import _count_categories


def test_object_labels_counted_by_category() -> None:
    """Object track labels are counted into their occupancy categories."""
    tracks = [
        {"label": "person"},
        {"label": "person"},
        {"label": "car"},
        {"label": "dog"},
    ]
    counts = _count_categories(tracks)
    assert counts == {"person": 2, "vehicle": 1, "animal": 1}


def test_named_track_counts_face() -> None:
    """A recognised name on a track counts the face category."""
    counts = _count_categories([{"label": "person", "name": "张三"}])
    assert counts == {"person": 1, "face": 1}


def test_fall_flag_counts_fall() -> None:
    """A track flagged is_fall counts the fall category."""
    counts = _count_categories([{"label": "person", "is_fall": True}])
    assert counts == {"person": 1, "fall": 1}


def test_empty_and_unmapped() -> None:
    """No tracks means empty; unmapped labels are ignored."""
    assert _count_categories([]) == {}
    assert _count_categories([{"label": "gesture"}]) == {}
