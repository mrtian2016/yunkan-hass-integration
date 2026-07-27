"""Switch platform for the Yunkan integration.

A single switch per camera toggles AI detection. Detection is a Pro feature, so
on a free-tier server the toggle raises a clear error and surfaces a repair
issue rather than silently failing. The current state is always readable because
it rides on the (free-tier) camera object.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import YunkanConfigEntry
from .api import YunkanApiError, YunkanProRequiredError
from .const import (
    DETECTION_FEATURE_SWITCHES,
    GLOBAL_DETECTION_SWITCHES,
    PTZ_TRACK_FIELDS,
    RECORD_MODE_OFF,
    RECORD_MODE_ON,
)
from .coordinator import YunkanCoordinator
from .entity import YunkanCameraEntity, server_device_info
from .issues import async_raise_pro_required

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YunkanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yunkan switches from a config entry."""
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = []
    for camera_id, camera in coordinator.data.cameras.items():
        entities.append(YunkanDetectionSwitch(coordinator, camera_id))
        entities.append(YunkanRecordingSwitch(coordinator, camera_id))
        entities.extend(
            YunkanFeatureSwitch(coordinator, camera_id, feature)
            for feature in DETECTION_FEATURE_SWITCHES
        )
        if camera.get("has_ptz"):
            entities.append(YunkanAutotrackSwitch(coordinator, camera_id))
    async_add_entities(entities)

    # Server-wide detection switches live on the hub device. They need the
    # (admin-only) global settings; a non-admin account can neither read nor
    # change them. Add them reactively the first time the settings become known,
    # so a *transient* failure on the first poll (as opposed to a real non-admin
    # 403) doesn't hide all 8 switches until the user reloads the integration.
    added_global = False

    @callback
    def _add_global_switches() -> None:
        nonlocal added_global
        if added_global or not coordinator.data.global_settings_known:
            return
        added_global = True
        async_add_entities(
            YunkanGlobalFeatureSwitch(coordinator, feature)
            for feature in GLOBAL_DETECTION_SWITCHES
        )

    if coordinator.data.global_settings_known:
        _add_global_switches()
    else:
        entry.async_on_unload(coordinator.async_add_listener(_add_global_switches))


class YunkanDetectionSwitch(YunkanCameraEntity, SwitchEntity):
    """Toggle AI detection for a camera."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_translation_key = "detection"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:motion-sensor"

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the detection switch."""
        super().__init__(coordinator, camera_id)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_detection"

    @property
    def is_on(self) -> bool:
        """Return whether detection is enabled for the camera."""
        return bool(self._camera.get("detection_enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable detection."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable detection."""
        await self._async_set(False)

    async def _async_set(self, desired: bool) -> None:
        """Toggle detection only if the desired state differs from current.

        Held under the per-camera detection lock with a re-read inside it: the
        backend endpoint is a *flip*, so two concurrent toggles (a double-tap, or
        one automation targeting several entities) must not double-flip past the
        desired state.
        """
        async with self.coordinator.detection_lock(self._camera_id):
            if bool(self._camera.get("detection_enabled")) == desired:
                return
            try:
                result = await self.coordinator.client.async_toggle_detection(
                    self._camera_id
                )
            except YunkanProRequiredError as err:
                async_raise_pro_required(self.hass, err.license_status)
                raise HomeAssistantError(
                    "AI detection is a Pro feature; the server is on the free tier."
                ) from err
            except YunkanApiError as err:
                raise HomeAssistantError(f"Failed to toggle detection: {err}") from err
            # Reflect the authoritative new value immediately, then refresh.
            if isinstance(result, dict) and self._camera_id in self.coordinator.data.cameras:
                self.coordinator.data.cameras[self._camera_id]["detection_enabled"] = bool(
                    result.get("detection_enabled", desired)
                )
                self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class YunkanRecordingSwitch(YunkanCameraEntity, SwitchEntity):
    """Toggle recording for a camera (continuous vs disabled)."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_translation_key = "recording"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:record-rec"

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the recording switch."""
        super().__init__(coordinator, camera_id)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_recording"
        # Remember the last non-disabled mode (e.g. "motion") to restore it.
        self._last_enabled_mode = RECORD_MODE_ON

    @property
    def is_on(self) -> bool:
        """Return whether recording is enabled (any mode other than disabled)."""
        return (self._camera.get("record_mode") or RECORD_MODE_OFF).lower() != RECORD_MODE_OFF

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable recording, restoring the previous mode."""
        await self._async_set_mode(self._last_enabled_mode)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable recording."""
        await self._async_set_mode(RECORD_MODE_OFF)

    async def _async_set_mode(self, mode: str) -> None:
        """Update the camera's record_mode, remembering the mode being left."""
        current = (self._camera.get("record_mode") or RECORD_MODE_OFF).lower()
        if current == mode:
            return
        # Remember the enabled mode we're leaving (e.g. "motion") so a later
        # turn_on restores it instead of defaulting to continuous. Captured on
        # write rather than as a read side-effect of is_on.
        if current != RECORD_MODE_OFF:
            self._last_enabled_mode = current
        try:
            await self.coordinator.client.async_update_camera(
                self._camera_id, record_mode=mode
            )
        except YunkanApiError as err:
            raise HomeAssistantError(f"Failed to change recording mode: {err}") from err
        if self._camera_id in self.coordinator.data.cameras:
            self.coordinator.data.cameras[self._camera_id]["record_mode"] = mode
            self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class YunkanFeatureSwitch(YunkanCameraEntity, SwitchEntity):
    """Toggle one detection feature (object/audio/face/plate/pose/gesture/package).

    This is a per-camera override on top of the server-wide setting. The backend
    kill-switch means an override can only *disable* a feature for one camera; it
    cannot enable a feature that is off server-wide (that ``true`` is dropped). So
    the resolved state is ``global AND override``, and turning a switch on while
    the feature is known to be disabled server-wide is rejected with a clear
    error rather than silently reverting.

    Resolving the state needs the server-wide settings, which are admin-only. A
    non-admin account cannot read them, and guessing from the shipped defaults
    would show a confident "off" for a feature that is very likely running —
    next to the entities the feature gate (correctly) keeps visible. So when the
    settings are unknown the switch reports unavailable instead: an honest "no
    idea" rather than a wrong answer, matching YunkanGlobalFeatureSwitch.
    """

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: YunkanCoordinator, camera_id: str, feature: str
    ) -> None:
        """Initialise the detection-feature switch."""
        super().__init__(coordinator, camera_id)
        override_key, translation_key, icon, _default = DETECTION_FEATURE_SWITCHES[feature]
        # The backend override key is a flat dot-path, e.g. "object.enabled".
        self._feature = feature
        self._override_key = override_key
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_feature_{feature}"
        self._attr_translation_key = f"detect_{translation_key}"
        self._attr_icon = icon

    def _global_on(self) -> bool:
        """Return the server-wide enable state of this feature."""
        return self.coordinator.data.global_feature_enabled(self._feature)

    def _override(self) -> bool | None:
        """Return this camera's per-camera override for the feature, if any."""
        overrides = self._camera.get("detection_overrides") or {}
        return overrides.get(self._override_key)

    @property
    def available(self) -> bool:
        """Available only when the server-wide state behind the override is known."""
        return super().available and self.coordinator.data.global_settings_known

    @property
    def is_on(self) -> bool:
        """Return the resolved state: server-wide setting AND per-camera override.

        A per-camera override of ``False`` disables the feature; any other value
        (True or unset) defers to the server-wide setting, matching the backend
        kill-switch. Only meaningful while ``available`` — see the class docstring.
        """
        if self._override() is False:
            return False
        return self._global_on()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the feature for this camera (requires it enabled server-wide)."""
        # Only refuse when the server-wide state is actually known to be off.
        # With the settings unreadable, let the backend answer rather than
        # rejecting on a guessed shipped default.
        if self.coordinator.data.global_settings_known and not self._global_on():
            raise HomeAssistantError(
                f"'{self._feature}' detection is disabled server-wide; enable it "
                "on the Yunkan server device first, then per camera."
            )
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the feature for this camera."""
        await self._async_set(False)

    async def _async_set(self, desired: bool) -> None:
        """Merge the feature flag into detection_overrides and push it.

        Held under the per-camera overrides lock and re-reads the latest
        overrides inside it, so two feature switches toggled at once don't
        overwrite each other's change.
        """
        async with self.coordinator.overrides_lock(self._camera_id):
            # Skip the write only when the current state is actually known:
            # is_on rests on the server-wide setting, so with that unreadable it
            # would short-circuit "turn off" into a silent no-op.
            if self.coordinator.data.global_settings_known and self.is_on == desired:
                return
            overrides = copy.deepcopy(self._camera.get("detection_overrides") or {})
            overrides[self._override_key] = desired
            try:
                await self.coordinator.client.async_update_camera(
                    self._camera_id, detection_overrides=overrides
                )
            except YunkanApiError as err:
                raise HomeAssistantError(f"Failed to update detection: {err}") from err
            if self._camera_id in self.coordinator.data.cameras:
                self.coordinator.data.cameras[self._camera_id]["detection_overrides"] = (
                    overrides
                )
                self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class YunkanAutotrackSwitch(YunkanCameraEntity, SwitchEntity):
    """Toggle PTZ auto-tracking (person/vehicle/pet together)."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_translation_key = "ptz_autotrack"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:crosshairs-gps"

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the auto-tracking switch."""
        super().__init__(coordinator, camera_id)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_ptz_autotrack"

    @property
    def is_on(self) -> bool:
        """Return whether any auto-tracking target is enabled."""
        return any(self._camera.get(field) for field in PTZ_TRACK_FIELDS)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable auto-tracking for all target types."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable auto-tracking."""
        await self._async_set(False)

    async def _async_set(self, desired: bool) -> None:
        """Set all ptz_track_* fields together."""
        if self.is_on == desired:
            return
        fields = {field: desired for field in PTZ_TRACK_FIELDS}
        try:
            await self.coordinator.client.async_update_camera(self._camera_id, **fields)
        except YunkanApiError as err:
            raise HomeAssistantError(f"Failed to update auto-tracking: {err}") from err
        if self._camera_id in self.coordinator.data.cameras:
            self.coordinator.data.cameras[self._camera_id].update(fields)
            self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class YunkanGlobalFeatureSwitch(CoordinatorEntity[YunkanCoordinator], SwitchEntity):
    """Toggle a detection feature server-wide (on the Yunkan hub device).

    This flips a ``detection.*`` flag in the server's settings, the same control
    as the Web Admin detection page. For the per-feature toggles it is the master
    switch the per-camera switches sit under: a feature turned off here is off for
    every camera regardless of any per-camera override. ``motion`` is server-wide
    only (there is no per-camera motion boolean — motion is tuned per camera via
    sensitivity/ROI), mapping to ``detection.motion.emit_enabled``.
    """

    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: YunkanCoordinator, feature: str) -> None:
        """Initialise the server-wide detection switch."""
        super().__init__(coordinator)
        setting_key, translation_key, icon, default = GLOBAL_DETECTION_SWITCHES[feature]
        self._feature = feature
        self._setting_key = setting_key
        self._default = default
        self._entry_id = coordinator.entry.entry_id
        self._attr_unique_id = f"{self._entry_id}_global_feature_{feature}"
        self._attr_translation_key = f"global_detect_{translation_key}"
        self._attr_icon = icon

    @property
    def device_info(self) -> DeviceInfo:
        """Return the server (hub) device info."""
        return server_device_info(
            self._entry_id, self.coordinator.client.base_url, self.coordinator.data.version
        )

    @property
    def available(self) -> bool:
        """Return whether the last poll succeeded and global settings are known."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data.global_settings_known
        )

    @property
    def is_on(self) -> bool:
        """Return whether the feature is enabled server-wide."""
        return self.coordinator.data.global_setting_bool(self._setting_key, self._default)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the feature server-wide."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the feature server-wide."""
        await self._async_set(False)

    async def _async_set(self, desired: bool) -> None:
        """Push the server-wide feature flag via the settings bulk endpoint."""
        if self.is_on == desired:
            return
        try:
            await self.coordinator.client.async_update_settings(
                {self._setting_key: desired}
            )
        except YunkanApiError as err:
            raise HomeAssistantError(
                f"Failed to update server detection settings: {err}"
            ) from err
        # Reflect the new value immediately, then refresh to confirm.
        self.coordinator.data.global_settings[self._setting_key] = desired
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
