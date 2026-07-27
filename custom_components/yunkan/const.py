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

# Options (entry.options) — event snapshot rendering for image entities.
# Server-side processing on the backend snapshot endpoint: bounding boxes are
# drawn (annotate=1) and/or the image is cropped around the detected object
# (crop=1). Mirrors Frigate's snapshot bounding_box / crop options.
OPT_SNAPSHOT_BBOX: Final = "snapshot_bounding_box"
OPT_SNAPSHOT_CROP: Final = "snapshot_crop"
DEFAULT_SNAPSHOT_BBOX: Final = True
DEFAULT_SNAPSHOT_CROP: Final = False

# Defaults
DEFAULT_BASE_URL: Final = "http://homeassistant.local:23406"
# Verify TLS by default; users with a self-signed LAN certificate can opt out.
# (Ignored for plain http, which is the common LAN case.)
DEFAULT_VERIFY_SSL: Final = True

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

# Camera update (PUT, admin) — used for the recording switch (record_mode).
API_CAMERA_UPDATE: Final = "/api/cameras/{camera_id}"
RECORD_MODE_ON: Final = "continuous"
RECORD_MODE_OFF: Final = "disabled"

# Server-wide settings (admin). GET returns effective values keyed by dot-path;
# PUT bulk-updates a whitelist of dot-paths. Used for the global (server-wide)
# detection-feature switches and to resolve the per-camera switch states (a
# per-camera override can only *disable* a feature that is enabled server-wide).
API_SETTINGS_ALL: Final = "/api/settings/all"
API_SETTINGS_BULK: Final = "/api/settings/bulk"
# Global detection-feature enable path, formatted with the feature key
# (object/audio/face/plate/pose/gesture/package).
GLOBAL_FEATURE_SETTING_KEY: Final = "detection.{feature}.enabled"

# Recording export (viewer-tier).
API_EXPORT: Final = "/api/recordings/export"

# Birdseye overview stream (admin, requires detection.birdseye.enabled).
API_BIRDSEYE_GRANT: Final = "/api/birdseye/live-grant"
BIRDSEYE_CAMERA_ID: Final = "birdseye"

# Per-camera live detection tracks (SSE) — drives occupancy.
API_LIVE_TRACKS: Final = "/api/cameras/{camera_id}/live-tracks"
# Clear occupancy if no track frame arrives for this long (detection stopped).
LIVE_TRACKS_STALE: Final = 8.0

# PTZ (viewer-tier, admin only) — direction/stop/presets.
API_PTZ_MOVE: Final = "/api/cameras/{camera_id}/ptz"
API_PTZ_STOP: Final = "/api/cameras/{camera_id}/ptz/stop"
API_PTZ_PRESETS: Final = "/api/cameras/{camera_id}/presets"
API_PTZ_PRESET_GOTO: Final = "/api/cameras/{camera_id}/presets/{preset_token}/goto"

# Recording timelapse (viewer-tier).
API_TIMELAPSE: Final = "/api/recordings/timelapse"

# Per-camera detection feature switches, backed by camera.detection_overrides.
# The backend override keys are FLAT dot-paths (e.g. "object.enabled").
# key -> (flat override key, translation_key, icon, global default enabled)
DETECTION_FEATURE_SWITCHES: Final[dict[str, tuple[str, str, str, bool]]] = {
    "object": ("object.enabled", "object", "mdi:cube-scan", True),
    "audio": ("audio.enabled", "audio", "mdi:microphone", False),
    "face": ("face.enabled", "face", "mdi:face-recognition", False),
    "plate": ("plate.enabled", "plate", "mdi:car-info", False),
    "pose": ("pose.enabled", "pose", "mdi:human-handsdown", False),
    "gesture": ("gesture.enabled", "gesture", "mdi:hand-wave", False),
    "package": ("package.enabled", "package", "mdi:package-variant-closed", False),
}

# Server-wide detection switches on the hub device, backed by GET/PUT /settings.
# These are the 7 per-feature toggles (the master control the per-camera
# overrides sit under) plus **motion** — which has no per-camera boolean in the
# backend (motion is tuned per camera via sensitivity/ROI, not on/off), so it is
# exposed server-wide only, mapping to detection.motion.emit_enabled ("report
# motion as its own event").
# key -> (settings dot-path, translation_key, icon, shipped default)
GLOBAL_DETECTION_SWITCHES: Final[dict[str, tuple[str, str, str, bool]]] = {
    "object": ("detection.object.enabled", "object", "mdi:cube-scan", True),
    "audio": ("detection.audio.enabled", "audio", "mdi:microphone", False),
    "face": ("detection.face.enabled", "face", "mdi:face-recognition", False),
    "plate": ("detection.plate.enabled", "plate", "mdi:car-info", False),
    "pose": ("detection.pose.enabled", "pose", "mdi:human-handsdown", False),
    "gesture": ("detection.gesture.enabled", "gesture", "mdi:hand-wave", False),
    "package": ("detection.package.enabled", "package", "mdi:package-variant-closed", False),
    "motion": ("detection.motion.emit_enabled", "motion", "mdi:motion-sensor", True),
}

# PTZ auto-tracking is backed by these camera fields (toggled together).
PTZ_TRACK_FIELDS: Final = ("ptz_track_person", "ptz_track_vehicle", "ptz_track_pet")

# Object categories that get a live "count" sensor (countable objects only).
OBJECT_COUNT_CATEGORIES: Final = ("person", "vehicle", "animal", "package")
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
    # The detector emits arrival/removal; both drive the package sensor.
    "package_arrival": "package",
    "package_removal": "package",
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
