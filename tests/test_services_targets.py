"""Tests for service target resolution across Home Assistant versions.

HA 2026.8.0 removed ``async_extract_referenced_entity_ids`` from
``homeassistant.helpers.service`` in favour of ``homeassistant.helpers.target``,
which broke setup outright on that version. ``services.py`` now picks the right
helper at import time, so these tests pin the behaviour that both branches must
produce: entity-, device- and area-targeted calls all resolve to the owning
coordinator and backend camera id.
"""

from __future__ import annotations

import inspect

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.yunkan.const import DOMAIN
from custom_components.yunkan.services import _resolve_targets

# ServiceCall only started taking ``hass`` as its first argument in HA 2025.x;
# on the oldest supported release (2024.11) it does not. Detect it so these
# tests run across the whole supported range.
_SERVICE_CALL_TAKES_HASS = "hass" in inspect.signature(ServiceCall.__init__).parameters


class FakeCoordinator:
    """Stand-in for YunkanCoordinator; only its identity matters here."""


def _make_call(hass: HomeAssistant, data: dict) -> ServiceCall:
    if _SERVICE_CALL_TAKES_HASS:
        return ServiceCall(hass, DOMAIN, "ptz", data)
    return ServiceCall(DOMAIN, "ptz", data)


@pytest.fixture
def yunkan_camera(hass: HomeAssistant):
    """Register a Yunkan config entry owning one camera in an area."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Yunkan")
    entry.add_to_hass(hass)
    coordinator = FakeCoordinator()
    entry.runtime_data = coordinator

    area = ar.async_get(hass).async_get_or_create("Living Room")

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "cam-1")},
        name="Front Door",
    )
    dev_reg.async_update_device(device.id, area_id=area.id)

    camera = er.async_get(hass).async_get_or_create(
        "camera",
        DOMAIN,
        f"{entry.entry_id}_cam-1_camera",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="front_door",
    )
    return coordinator, camera, device, area


async def test_resolve_by_entity_id(hass: HomeAssistant, yunkan_camera) -> None:
    coordinator, camera, _, _ = yunkan_camera
    targets = _resolve_targets(hass, _make_call(hass, {"entity_id": camera.entity_id}))
    assert targets == [(coordinator, "cam-1")]


async def test_resolve_by_device_id(hass: HomeAssistant, yunkan_camera) -> None:
    coordinator, _, device, _ = yunkan_camera
    targets = _resolve_targets(hass, _make_call(hass, {"device_id": device.id}))
    assert targets == [(coordinator, "cam-1")]


async def test_resolve_by_area_id(hass: HomeAssistant, yunkan_camera) -> None:
    coordinator, _, _, area = yunkan_camera
    targets = _resolve_targets(hass, _make_call(hass, {"area_id": area.id}))
    assert targets == [(coordinator, "cam-1")]


async def test_entity_and_device_together_dedupe(
    hass: HomeAssistant, yunkan_camera
) -> None:
    coordinator, camera, device, _ = yunkan_camera
    targets = _resolve_targets(
        hass,
        _make_call(hass, {"entity_id": camera.entity_id, "device_id": device.id}),
    )
    assert targets == [(coordinator, "cam-1")]


async def test_non_yunkan_target_raises(hass: HomeAssistant, yunkan_camera) -> None:
    with pytest.raises(ServiceValidationError):
        _resolve_targets(hass, _make_call(hass, {"entity_id": "camera.not_yunkan"}))
