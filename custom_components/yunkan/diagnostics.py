"""Diagnostics support for the Yunkan integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import YunkanConfigEntry
from .const import CONF_PASSWORD

_REDACTED = "**REDACTED**"

# Exact keys to redact.
TO_REDACT = frozenset(
    {
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
)

# Safety net: redact anything that looks like a secret even if a new backend
# field appears later, so diagnostics can't silently leak it.
_REDACT_SUFFIXES = ("_pass", "_password", "_secret", "_key", "_token", "_cookies")
_REDACT_PREFIXES = ("rtsp_", "onvif_")


def _is_sensitive(key: str) -> bool:
    """Return whether a key name should be redacted."""
    lower = key.lower()
    return (
        key in TO_REDACT
        or lower.endswith(_REDACT_SUFFIXES)
        or lower.startswith(_REDACT_PREFIXES)
    )


def _redact(obj: Any) -> Any:
    """Recursively redact sensitive keys from dicts/lists."""
    if isinstance(obj, dict):
        return {k: (_REDACTED if _is_sensitive(k) else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(item) for item in obj]
    return obj


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: YunkanConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": {
            "title": entry.title,
            "data": _redact(dict(entry.data)),
        },
        "license": _redact(dict(data.license)),
        "version": data.version.get("current", {}),
        "sse": {
            "connected": coordinator.sse.connected,
            "last_message_age": coordinator.sse.last_message_age,
        },
        "camera_count": len(data.cameras),
        "cameras": [_redact(camera) for camera in data.cameras.values()],
    }
