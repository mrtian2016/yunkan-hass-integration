"""Select platform for the Yunkan integration.

A PTZ **preset** dropdown per pan/tilt/zoom camera: the options are the presets
stored on the camera, and picking one moves the camera there. This replaces a
button-per-preset, which got noisy on cameras with many presets.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YunkanConfigEntry
from .api import YunkanApiError
from .coordinator import YunkanCoordinator
from .entity import YunkanCameraEntity

_LOGGER = logging.getLogger(__name__)

_PTZ_PRESET_SPEED = 0.5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YunkanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yunkan select entities (PTZ preset dropdowns) from a config entry."""
    coordinator = entry.runtime_data
    ptz_ids = [
        camera_id
        for camera_id, camera in coordinator.data.cameras.items()
        if camera.get("has_ptz")
    ]
    if not ptz_ids:
        return
    # Fetch presets concurrently so one slow/unreachable camera doesn't serialise
    # the whole platform setup (each call has a 30s client timeout).
    results = await asyncio.gather(
        *(coordinator.client.async_ptz_presets(camera_id) for camera_id in ptz_ids),
        return_exceptions=True,
    )
    entities: list[SelectEntity] = []
    for camera_id, presets in zip(ptz_ids, results):
        if isinstance(presets, Exception):
            _LOGGER.debug("presets unavailable for %s: %s", camera_id, presets)
            continue
        # Map display name -> token, keeping insertion order and dropping dupes.
        mapping: dict[str, str] = {}
        for item in presets:
            if not isinstance(item, dict):
                continue
            token = item.get("token")
            if token is None:
                continue
            name = str(item.get("name") or token)
            mapping.setdefault(name, str(token))
        if mapping:
            entities.append(YunkanPtzPresetSelect(coordinator, camera_id, mapping))
    async_add_entities(entities)


class YunkanPtzPresetSelect(YunkanCameraEntity, SelectEntity):
    """Go-to-preset dropdown for a PTZ camera."""

    _attr_translation_key = "ptz_preset"
    _attr_icon = "mdi:map-marker"

    def __init__(
        self, coordinator: YunkanCoordinator, camera_id: str, presets: dict[str, str]
    ) -> None:
        """Initialise the preset dropdown (options come from the camera)."""
        super().__init__(coordinator, camera_id)
        self._presets = presets  # display name -> token
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_ptz_preset"
        self._attr_options = list(presets)
        # The camera doesn't report which preset it's currently at; the state
        # reflects the last preset commanded from HA (None until first use).
        self._attr_current_option = None

    async def async_select_option(self, option: str) -> None:
        """Move the camera to the chosen preset."""
        token = self._presets.get(option)
        if token is None:
            raise HomeAssistantError(f"Unknown preset: {option}")
        try:
            await self.coordinator.client.async_ptz_goto_preset(
                self._camera_id, token, _PTZ_PRESET_SPEED
            )
        except YunkanApiError as err:
            raise HomeAssistantError(f"PTZ preset failed: {err}") from err
        self._attr_current_option = option
        self.async_write_ha_state()
