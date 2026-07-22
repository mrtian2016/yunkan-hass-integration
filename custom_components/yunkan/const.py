"""Constants for the Yunkan integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "yunkan"

# Config entry keys
CONF_BASE_URL: Final = "base_url"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_VERIFY_SSL: Final = "verify_ssl"

# Runtime data / hass.data keys
DATA_CLIENT: Final = "client"
DATA_COORDINATOR: Final = "coordinator"
DATA_SSE: Final = "sse"

# Defaults
DEFAULT_BASE_URL: Final = "http://homeassistant.local:23406"
DEFAULT_VERIFY_SSL: Final = False

# Backend REST endpoints (all mounted behind the nginx :23406 entry).
# Prefixes are hard-coded per router on the backend; these are the full paths.
API_SETUP_STATUS: Final = "/api/auth/setup-status"
API_LOGIN: Final = "/api/auth/login"
API_ME: Final = "/api/auth/me"
API_LICENSE_STATUS: Final = "/api/license/status"
API_SYSTEM_VERSION: Final = "/api/system/version"
API_HEALTHZ: Final = "/healthz"

# The backend wraps every success as {"code": 0, "data": ..., "message": ...}
# and every error as {"code": <http_status>, "data": null, "message": <detail>}.
RESPONSE_CODE_OK: Final = 0

# Pro gating: Pro-only endpoints answer 403 with message "LICENSE_REQUIRED:<status>".
LICENSE_REQUIRED_PREFIX: Final = "LICENSE_REQUIRED:"
SETUP_REQUIRED_MESSAGE: Final = "SETUP_REQUIRED"

# Camera / streaming endpoints.
API_CAMERAS: Final = "/api/cameras"
API_CAMERA_SNAPSHOT: Final = "/api/cameras/{camera_id}/snapshot"
API_LIVE_GRANT: Final = "/api/cameras/{camera_id}/live-grant"
API_DETECTION_STATUS: Final = "/api/detection/status"
API_DETECTION_TOGGLE: Final = "/api/detection/toggle/{camera_id}"

# PTZ (viewer-tier, admin only) — direction/stop/presets.
API_PTZ_MOVE: Final = "/api/cameras/{camera_id}/ptz"
API_PTZ_STOP: Final = "/api/cameras/{camera_id}/ptz/stop"
API_PTZ_PRESET_GOTO: Final = "/api/cameras/{camera_id}/presets/{preset_token}/goto"
PTZ_DIRECTIONS: Final = (
    "up",
    "down",
    "left",
    "right",
    "up_left",
    "up_right",
    "down_left",
    "down_right",
    "zoom_in",
    "zoom_out",
)

# TTS broadcast (Pro-gated, admin only). "tts/test" is the only REST entry that
# synthesises text and pushes it to a camera speaker.
API_TTS_BROADCAST: Final = "/api/cameras/{camera_id}/talkback/tts/test"

# Events / SSE.
API_EVENTS: Final = "/api/events"
API_EVENT_DETAIL: Final = "/api/events/{event_id}"
API_EVENTS_STREAM: Final = "/api/events/stream"
API_EVENT_SNAPSHOT: Final = "/api/events/snapshot"
API_SSE_TICKET: Final = "/api/auth/sse-ticket"

# SSE keepalive is 15s; treat the stream as stale after a comfortable margin.
SSE_STALL_TIMEOUT: Final = 45.0

# Endpoints that answer 403 LICENSE_REQUIRED on the free tier.
PRO_GATED_ENDPOINTS: Final = ("detection", "talkback", "tts")

# Player URL templates (relative to the nginx :23406 base the user configured).
# WHEP: POST the SDP offer here with Content-Type: application/sdp, get the answer.
WHEP_PATH: Final = "/index/api/whep"
HLS_PATH: Final = "/{app}/{stream}/hls.m3u8"

# live-grant token TTL is 1800s; refresh at ~80% of the TTL to stay gapless.
LIVE_GRANT_REFRESH_RATIO: Final = 0.8

# --- Event category mapping (mirrors backend actions/mqtt.py) ---
# Raw detection event_type -> merged binary_sensor category. Event types absent
# from this map (gesture / plate / motion / package_arrival ...) are intentionally
# NOT surfaced as per-camera occupancy sensors, matching MQTT discovery behaviour.
EVENT_CATEGORY_MAP: Final[dict[str, str]] = {
    "person": "person",
    "car": "vehicle",
    "truck": "vehicle",
    "bus": "vehicle",
    "motorbike": "vehicle",
    "motorcycle": "vehicle",
    "bicycle": "vehicle",
    "vehicle": "vehicle",
    "dog": "animal",
    "cat": "animal",
    "bird": "animal",
    "package": "package",
    "face": "face",
    "fall": "fall",
    "cry": "cry",
}

# category -> (English name, HA device_class, icon). Mirrors CATEGORY_META.
CATEGORY_META: Final[dict[str, tuple[str, str, str]]] = {
    "person": ("Person", "motion", "mdi:walk"),
    "vehicle": ("Vehicle", "motion", "mdi:car"),
    "animal": ("Animal", "motion", "mdi:paw"),
    "package": ("Package", "occupancy", "mdi:package-variant-closed"),
    "face": ("Face", "occupancy", "mdi:face-recognition"),
    "fall": ("Fall", "safety", "mdi:human-handsdown"),
    "cry": ("Baby Cry", "sound", "mdi:emoticon-cry-outline"),
}

# Ordered categories used to lay out binary_sensor entities per camera.
EVENT_CATEGORIES: Final = tuple(CATEGORY_META.keys())

# Detection events are instantaneous (only an ON edge is published); auto-reset
# each category sensor after this many seconds, matching CAMERA_EVENT_OFF_DELAY_SEC.
EVENT_OFF_DELAY: Final = 30.0

# Manufacturer / model strings for the HA device registry.
MANUFACTURER: Final = "Yunkan"
