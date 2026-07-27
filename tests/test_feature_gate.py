"""Tests for deciding which detection features a camera exposes entities for."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.yunkan.const import (
    CATEGORY_FEATURE,
    CATEGORY_META,
    DETECTION_FEATURE_SWITCHES,
    OBJECT_COUNT_CATEGORIES,
)
from custom_components.yunkan.coordinator import YunkanData


def _data(overrides: dict | None = None, global_settings: dict | None = None) -> YunkanData:
    """Build a YunkanData holding one camera with the given override state."""
    camera = {"id": "cam_1", "name": "Hall"}
    if overrides is not None:
        camera["detection_overrides"] = overrides
    return YunkanData(
        cameras={"cam_1": camera},
        global_settings=global_settings if global_settings is not None else {},
    )


def test_every_category_maps_to_a_real_feature() -> None:
    """Each category is produced by a feature that has a switch behind it."""
    assert set(CATEGORY_FEATURE) == set(CATEGORY_META)
    for feature in CATEGORY_FEATURE.values():
        assert feature in DETECTION_FEATURE_SWITCHES
    for category in OBJECT_COUNT_CATEGORIES:
        assert category in CATEGORY_FEATURE


def test_recognition_sensor_features_are_real() -> None:
    """The features gating the recognition sensors must be real switch keys.

    These are hard-coded in sensor.async_setup_entry; a typo or a renamed
    feature would raise KeyError inside the gate's first sync and take down the
    whole sensor platform, not just one entity.
    """
    for feature in ("face", "plate", "gesture"):
        assert feature in DETECTION_FEATURE_SWITCHES


def test_device_triggers_are_all_translated() -> None:
    """Every device trigger type has a label in all shipped translations.

    Adding a category silently adds a device trigger (TRIGGER_TYPES is derived
    from EVENT_CATEGORIES); without this check the automation UI shows the raw
    key, which is exactly what happened when gesture was added.
    """
    root = Path(__file__).resolve().parent.parent / "custom_components" / "yunkan"
    expected = {f"{category}_detected" for category in CATEGORY_META}
    files = ["strings.json"] + [
        f"translations/{lang}.json" for lang in ("en", "zh-Hans", "zh-Hant", "ja", "fr")
    ]
    for rel in files:
        data = json.loads((root / rel).read_text(encoding="utf-8"))
        labels = data["device_automation"]["trigger_type"]
        assert set(labels) == expected, f"{rel} trigger_type mismatch"
        assert all(labels.values()), f"{rel} has an empty trigger label"


def test_exposed_when_enabled_server_wide() -> None:
    """A feature on server-wide with no override is exposed."""
    data = _data(global_settings={"detection.face.enabled": True})
    assert data.should_expose_feature("cam_1", "face") is True


def test_hidden_when_disabled_server_wide() -> None:
    """A feature off server-wide is not exposed."""
    data = _data(global_settings={"detection.face.enabled": False})
    assert data.should_expose_feature("cam_1", "face") is False


def test_hidden_when_camera_override_disables() -> None:
    """A per-camera override of False hides the feature even if on globally."""
    data = _data(
        overrides={"face.enabled": False},
        global_settings={"detection.face.enabled": True},
    )
    assert data.should_expose_feature("cam_1", "face") is False


def test_camera_override_cannot_enable_past_the_kill_switch() -> None:
    """An override of True cannot resurrect a feature disabled server-wide.

    Mirrors the backend kill-switch: such a ``true`` is dropped on write.
    """
    data = _data(
        overrides={"face.enabled": True},
        global_settings={"detection.face.enabled": False},
    )
    assert data.should_expose_feature("cam_1", "face") is False


def test_unknown_global_settings_assume_enabled() -> None:
    """A non-admin account cannot read settings; assume enabled, don't guess off.

    Hiding entities that are in fact live is far worse than showing a quiet one,
    so this deliberately differs from the feature switch (which falls back to the
    shipped defaults — face ships disabled).
    """
    data = _data()
    assert data.global_settings_known is False
    assert data.should_expose_feature("cam_1", "face") is True
    # An explicit per-camera "off" is still authoritative — it needs no settings.
    off = _data(overrides={"face.enabled": False})
    assert off.should_expose_feature("cam_1", "face") is False


def test_unknown_camera_is_not_exposed() -> None:
    """A camera that is gone (removed / archived) exposes nothing.

    Note this is the *predicate's* answer. The gate itself treats an absent
    camera as "leave whatever is already there alone" (see feature_gate._sync)
    so that a camera disappearing from one poll doesn't strip half of its
    device page while the always-present entities merely go unavailable.
    """
    data = _data(global_settings={"detection.face.enabled": True})
    assert data.should_expose_feature("cam_missing", "face") is False
