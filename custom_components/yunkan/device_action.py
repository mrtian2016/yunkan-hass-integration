"""Device automation actions for the Yunkan integration.

Surfaces the integration's services as **device actions**, so they appear scoped
to a camera device in the automation editor's "Then do" step (the counterpart to
the device triggers). Each action is a thin wrapper that calls the matching
service targeting the device — including the ones that need free-form input
(voice broadcast, export), which can't be one-tap buttons.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_TYPE
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType, TemplateVarsType

from .const import DOMAIN, PTZ_DIRECTIONS
from .device_trigger import _camera_ref
from .services import (
    ATTR_DIRECTION,
    ATTR_DURATION,
    ATTR_END,
    ATTR_FILENAME,
    ATTR_MESSAGE,
    ATTR_PITCH,
    ATTR_PLAYBACK_FACTOR,
    ATTR_PRESET,
    ATTR_RATE,
    ATTR_SPEED,
    ATTR_START,
    ATTR_VOICE,
    PLAYBACK_REALTIME,
    PLAYBACK_TIMELAPSE,
    SERVICE_EXPORT,
    SERVICE_PTZ,
    SERVICE_SNAPSHOT,
    SERVICE_TTS_BROADCAST,
)

# action type -> (service, forwarded field names). Order = display order.
_ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "ptz_move": (SERVICE_PTZ, (ATTR_DIRECTION, ATTR_SPEED, ATTR_DURATION)),
    "ptz_preset": (SERVICE_PTZ, (ATTR_PRESET, ATTR_SPEED)),
    "tts_broadcast": (
        SERVICE_TTS_BROADCAST,
        (ATTR_MESSAGE, ATTR_VOICE, ATTR_RATE, ATTR_PITCH),
    ),
    "snapshot": (SERVICE_SNAPSHOT, (ATTR_FILENAME,)),
    "export": (SERVICE_EXPORT, (ATTR_START, ATTR_END, ATTR_PLAYBACK_FACTOR)),
}
ACTION_TYPES = set(_ACTIONS)

ACTION_SCHEMA = cv.DEVICE_ACTION_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(ACTION_TYPES)},
    extra=vol.ALLOW_EXTRA,
)

_SPEED = vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0))

# Extra fields per action, rendered as a form in the automation editor.
_CAPABILITIES: dict[str, vol.Schema] = {
    "ptz_move": vol.Schema(
        {
            vol.Required(ATTR_DIRECTION): vol.In(list(PTZ_DIRECTIONS)),
            vol.Optional(ATTR_SPEED, default=0.5): _SPEED,
            vol.Optional(ATTR_DURATION, default=0.5): vol.All(
                vol.Coerce(float), vol.Range(min=0.0, max=10.0)
            ),
        }
    ),
    "ptz_preset": vol.Schema(
        {
            vol.Required(ATTR_PRESET): cv.string,
            vol.Optional(ATTR_SPEED, default=0.5): _SPEED,
        }
    ),
    "tts_broadcast": vol.Schema(
        {
            vol.Required(ATTR_MESSAGE): cv.string,
            vol.Optional(ATTR_VOICE): cv.string,
            vol.Optional(ATTR_RATE): cv.string,
            vol.Optional(ATTR_PITCH): cv.string,
        }
    ),
    "snapshot": vol.Schema({vol.Required(ATTR_FILENAME): cv.string}),
    "export": vol.Schema(
        {
            vol.Required(ATTR_START): cv.datetime,
            vol.Required(ATTR_END): cv.datetime,
            vol.Optional(ATTR_PLAYBACK_FACTOR, default=PLAYBACK_REALTIME): vol.In(
                [PLAYBACK_REALTIME, PLAYBACK_TIMELAPSE]
            ),
        }
    ),
}


def _action(device_id: str, action_type: str) -> dict[str, Any]:
    return {CONF_DOMAIN: DOMAIN, CONF_DEVICE_ID: device_id, CONF_TYPE: action_type}


async def async_get_actions(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List device actions for a Yunkan camera device.

    Gate the capability-specific actions (PTZ / voice broadcast) on the camera's
    features so a non-PTZ / speaker-less camera doesn't offer them. If the camera
    can't be resolved (entry not loaded yet), fall back to offering everything —
    unsupported ones just fail gracefully at call time.
    """
    ref = _camera_ref(hass, device_id)
    if ref is None:
        return []
    entry_id, camera_id = ref
    entry = hass.config_entries.async_get_entry(entry_id)
    coordinator = getattr(entry, "runtime_data", None)
    camera = coordinator.data.cameras.get(camera_id) if coordinator is not None else None
    if camera is None:
        return [_action(device_id, action_type) for action_type in _ACTIONS]

    has_ptz = bool(camera.get("has_ptz"))
    talkback = bool(camera.get("talkback_supported"))
    actions: list[dict[str, Any]] = []
    for action_type in _ACTIONS:
        if action_type in ("ptz_move", "ptz_preset") and not has_ptz:
            continue
        if action_type == "tts_broadcast" and not talkback:
            continue
        actions.append(_action(device_id, action_type))
    return actions


async def async_get_action_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """Return the extra input fields for an action type."""
    schema = _CAPABILITIES.get(config[CONF_TYPE])
    return {"extra_fields": schema} if schema is not None else {}


async def async_call_action_from_config(
    hass: HomeAssistant,
    config: ConfigType,
    variables: TemplateVarsType,
    context: Context | None,
) -> None:
    """Execute a device action by calling the matching service on the device."""
    service, fields = _ACTIONS[config[CONF_TYPE]]
    data: dict[str, Any] = {
        field: config[field] for field in fields if field in config
    }
    data[CONF_DEVICE_ID] = config[CONF_DEVICE_ID]
    await hass.services.async_call(
        DOMAIN, service, data, blocking=True, context=context
    )
