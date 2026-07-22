"""Diagnostics support for the Yunkan integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import YunkanConfigEntry
from .const import CONF_PASSWORD

TO_REDACT = {
    CONF_PASSWORD,
    "access_token",
    "token",
    "rtsp_main",
    "rtsp_sub",
    "onvif_url",
    "onvif_user",
    "onvif_pass",
    "live_proxy_key",
    "detect_proxy_key",
    "vendor_api_user",
    "vendor_api_pass",
    "customer_email",
    "license_id",
    "machine_id_hash",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: YunkanConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    license_info = dict(data.license)
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
        },
        "license": async_redact_data(license_info, TO_REDACT),
        "version": data.version.get("current", {}),
        "sse": {
            "connected": coordinator.sse.connected,
            "last_message_age": coordinator.sse.last_message_age,
        },
        "camera_count": len(data.cameras),
        "cameras": [
            async_redact_data(camera, TO_REDACT) for camera in data.cameras.values()
        ],
    }
