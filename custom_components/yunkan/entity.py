"""Shared entity helpers for the Yunkan integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import YunkanCoordinator


def server_device_info(entry_id: str, base_url: str, version: dict[str, Any]) -> DeviceInfo:
    """Build the device info for the Yunkan server (the config-entry hub)."""
    current = version.get("current") if isinstance(version, dict) else None
    sw_version = current.get("backend") if isinstance(current, dict) else None
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name="Yunkan",
        manufacturer=MANUFACTURER,
        model="VMS",
        sw_version=sw_version,
        configuration_url=base_url,
    )


def camera_device_info(entry_id: str, camera: dict[str, Any]) -> DeviceInfo:
    """Build the device info for a single camera."""
    camera_id = camera["id"]
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{camera_id}")},
        name=camera.get("name") or camera_id,
        manufacturer=camera.get("manufacturer") or MANUFACTURER,
        model=camera.get("model") or camera.get("source_type"),
        sw_version=camera.get("firmware_version"),
        serial_number=camera.get("serial_number"),
        via_device=(DOMAIN, entry_id),
    )


class YunkanCameraEntity(CoordinatorEntity[YunkanCoordinator]):
    """Base entity bound to a Yunkan camera device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the entity for a camera id."""
        super().__init__(coordinator)
        self._camera_id = camera_id
        self._entry_id = coordinator.entry.entry_id

    @property
    def _camera(self) -> dict[str, Any]:
        """Return the current camera object from the coordinator data."""
        return self.coordinator.data.cameras.get(self._camera_id, {})

    @property
    def device_info(self) -> DeviceInfo:
        """Return the camera device info."""
        return camera_device_info(self._entry_id, self._camera or {"id": self._camera_id})

    @property
    def available(self) -> bool:
        """Return whether the camera is still present and the poll succeeded."""
        return self.coordinator.last_update_success and self._camera_id in (
            self.coordinator.data.cameras
        )
