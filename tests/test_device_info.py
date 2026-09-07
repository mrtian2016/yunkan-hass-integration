"""Tests for how a camera device declares the hub it belongs to.

Home Assistant 2026.9 removed ``DeviceInfo["via_device"]`` in favour of
``via_device_id`` and made the old key report deprecated usage. That report
raises when it cannot attribute the call to an integration, which is the case
for every camera added after the platform's first await — so passing the old
key on a core that has moved on silently drops most of the cameras. These tests
pin both branches so neither can come back by accident.
"""

from __future__ import annotations

from custom_components.yunkan import entity as entity_module
from custom_components.yunkan.const import DOMAIN, MANUFACTURER
from custom_components.yunkan.entity import camera_device_info

CAMERA = {"id": "cam_1", "name": "Hall", "manufacturer": "Acme", "model": "X1"}


def test_identity_fields_are_independent_of_the_hub_link() -> None:
    """The device is identified the same way on every core."""
    info = camera_device_info("entry_1", CAMERA, "hub_dev_id")
    assert info["identifiers"] == {(DOMAIN, "entry_1_cam_1")}
    assert info["name"] == "Hall"
    assert info["manufacturer"] == "Acme"
    assert info["model"] == "X1"


def test_named_by_id_when_the_camera_has_no_name() -> None:
    """A nameless camera still gets a usable device name."""
    info = camera_device_info("entry_1", {"id": "cam_2"}, "hub_dev_id")
    assert info["name"] == "cam_2"
    assert info["manufacturer"] == MANUFACTURER


def test_links_by_device_id_on_cores_that_support_it(monkeypatch) -> None:
    """Newer cores get the hub's registry id and never the removed key."""
    monkeypatch.setattr(entity_module, "_VIA_DEVICE_ID_SUPPORTED", True)
    info = camera_device_info("entry_1", CAMERA, "hub_dev_id")
    assert info["via_device_id"] == "hub_dev_id"
    assert "via_device" not in info


def test_no_hub_link_rather_than_a_deprecated_one(monkeypatch) -> None:
    """An unknown hub id leaves the device un-nested, never falls back."""
    monkeypatch.setattr(entity_module, "_VIA_DEVICE_ID_SUPPORTED", True)
    info = camera_device_info("entry_1", CAMERA, None)
    assert "via_device" not in info
    assert "via_device_id" not in info


def test_links_by_identifier_on_older_cores(monkeypatch) -> None:
    """Cores below 2026.9 only know the identifier tuple."""
    monkeypatch.setattr(entity_module, "_VIA_DEVICE_ID_SUPPORTED", False)
    info = camera_device_info("entry_1", CAMERA, "hub_dev_id")
    assert info["via_device"] == (DOMAIN, "entry_1")
    assert "via_device_id" not in info
