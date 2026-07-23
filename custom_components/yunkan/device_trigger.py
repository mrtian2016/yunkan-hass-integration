"""Device automation triggers for the Yunkan integration.

Exposes a "… detected" trigger per detection category on each camera device, so
users can build automations ("when a person is detected on the front door") from
the UI without wiring up the binary sensors themselves.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, EVENT_CATEGORIES, EVENT_CATEGORY_MAP
from .coordinator import signal_event
from .event_utils import event_attributes

TRIGGER_TYPES = {f"{category}_detected" for category in EVENT_CATEGORIES}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES)}
)


def _camera_ref(hass: HomeAssistant, device_id: str) -> tuple[str, str] | None:
    """Return (entry_id, camera_id) for a Yunkan camera device, or None."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    for domain, identifier in device.identifiers:
        # Camera devices are "{entry_id}_{camera_id}"; the hub is bare "{entry_id}".
        if domain == DOMAIN and "_" in identifier:
            entry_id, camera_id = identifier.split("_", 1)
            return entry_id, camera_id
    return None


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List device triggers for a Yunkan camera device."""
    if _camera_ref(hass, device_id) is None:
        return []
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: f"{category}_detected",
        }
        for category in EVENT_CATEGORIES
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a device trigger, firing the action on matching detection events."""
    ref = _camera_ref(hass, config[CONF_DEVICE_ID])
    if ref is None:
        return lambda: None
    entry_id, camera_id = ref
    category = config[CONF_TYPE].removesuffix("_detected")
    job = HassJob(action)
    job_data = trigger_info["trigger_data"]

    @callback
    def _handle_event(event: dict[str, Any]) -> None:
        if event.get("camera_id") != camera_id:
            return
        if EVENT_CATEGORY_MAP.get(event.get("event_type", "")) != category:
            return
        hass.async_run_hass_job(
            job,
            {
                "trigger": {
                    **job_data,
                    "platform": "device",
                    "domain": DOMAIN,
                    "device_id": config[CONF_DEVICE_ID],
                    "type": config[CONF_TYPE],
                    "category": category,
                    "camera_id": camera_id,
                    "event": event_attributes(event),
                }
            },
        )

    return async_dispatcher_connect(hass, signal_event(entry_id), _handle_event)
