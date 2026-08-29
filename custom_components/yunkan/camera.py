"""Camera platform for the Yunkan integration.

Each Yunkan camera becomes a native HA camera entity, plus an optional birdseye
overview camera when the server has it enabled:
* snapshots come from the backend's JPEG snapshot endpoint;
* live view uses HA's native WebRTC path (2024.11+) by relaying the browser's
  SDP offer to the server's WHEP endpoint and returning the answer;
* ``stream_source`` prefers a direct RTSP URL (sub-second latency, no HLS
  segmentation/GOP dependency — what go2rtc / the WebRTC Camera card / the
  stream component consume best), probing the RTSP port once and falling back
  to the signed HLS URL when the port is unreachable (e.g. behind an HTTP-only
  reverse proxy). The frontend live view itself is WebRTC and unaffected.

All playback URLs are signed with a short-lived live-grant token minted per
session; nothing but the nginx entry (base URL) is ever exposed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlsplit

from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
    WebRTCAnswer,
    WebRTCClientConfiguration,
    WebRTCError,
    WebRTCSendMessage,
)

try:
    # Home Assistant vendors the ICE server model from webrtc-models.
    from webrtc_models import RTCIceServer
except ImportError:  # older cores re-exported it from the camera component
    from homeassistant.components.camera.webrtc import RTCIceServer

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import YunkanConfigEntry
from .api import YunkanApiError, YunkanConnectionError
from .const import BIRDSEYE_CAMERA_ID, RTSP_PORT_DEFAULT
from .coordinator import YunkanCoordinator
from .entity import YunkanCameraEntity, server_device_info
from .urls import build_hls_url, build_rtsp_url, build_whep_url, pick_live, pick_stream

_LOGGER = logging.getLogger(__name__)

# RTSP reachability probe cache: {(host, port): (reachable, monotonic_ts)}.
# One TCP connect per host/port per _RTSP_PROBE_TTL — all cameras of an entry
# share the same server, so this is at most one probe every few minutes.
_RTSP_PROBE_TTL = 600.0
_rtsp_probe_cache: dict[tuple[str, int], tuple[bool, float]] = {}


async def _rtsp_port_reachable(host: str, port: int) -> bool:
    """Cheap cached TCP-connect probe of the server's RTSP port.

    The RTSP port is not proxied by nginx: on LAN/host-network deployments it
    is reachable and RTSP is strictly better for stream consumers; behind an
    HTTP-only reverse proxy it is not, and we must stay on HLS. A 2s connect
    probe cached for 10 minutes decides — no user-facing knob.
    """
    if not host:
        return False
    key = (host, port)
    cached = _rtsp_probe_cache.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[1] < _RTSP_PROBE_TTL:
        return cached[0]
    ok = False
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=2.0)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 - close errors are irrelevant to the probe
            pass
        ok = True
    except Exception:  # noqa: BLE001 - unreachable/timeout/refused all mean "use HLS"
        ok = False
    _rtsp_probe_cache[key] = (ok, now)
    return ok


# Reuse a live-grant across the client-config + offer handshake instead of
# minting one per call. Comfortably shorter than the 1800s server TTL.
_GRANT_REUSE_SEC = 600.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YunkanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yunkan cameras (and birdseye, if enabled) from a config entry."""
    coordinator = entry.runtime_data
    entities: list[Camera] = [
        YunkanCamera(coordinator, camera_id) for camera_id in coordinator.data.cameras
    ]
    # Add the birdseye overview camera only when the server has it enabled.
    try:
        await coordinator.client.async_birdseye_grant()
    except YunkanConnectionError as err:
        # A transient blip must not permanently drop birdseye until reload —
        # retry setup instead of misreading it as "not enabled".
        raise ConfigEntryNotReady(f"Yunkan server unreachable: {err}") from err
    except YunkanApiError:
        _LOGGER.debug("birdseye not available; skipping overview camera")
    else:
        entities.append(YunkanBirdseyeCamera(coordinator))
    async_add_entities(entities)


class _YunkanStreamCamera(Camera):
    """Base camera providing grant-based native WebRTC (WHEP) + HLS streaming.

    Subclasses supply ``_stream_label`` and ``_fetch_grant()``. ``self.coordinator``
    is provided by the concrete entity base (CoordinatorEntity).
    """

    _attr_supported_features = CameraEntityFeature.STREAM

    coordinator: YunkanCoordinator
    _stream_label: str

    def _init_stream(self) -> None:
        """Initialise the grant cache (call from the subclass __init__)."""
        Camera.__init__(self)
        self._grant: dict[str, Any] | None = None
        self._grant_ts: float = 0.0
        # Resolved stream transport, surfaced as an entity attribute so users can
        # SEE which path stream consumers get (rtsp = direct low-latency engine
        # port; hls = signed HTTP fallback). Never includes the token.
        self._stream_transport: str | None = None

    async def async_added_to_hass(self) -> None:
        """Warm the grant so the first WebRTC config carries the server ICE servers."""
        await super().async_added_to_hass()
        try:
            await self._async_grant()
        except YunkanApiError:
            pass  # streaming will retry on demand
        # Pre-resolve the transport attribute so it is visible without anyone
        # having opened a stream yet (probe result is cached module-wide).
        try:
            await self.stream_source()
        except Exception:  # noqa: BLE001 - attribute warm-up must never break setup
            pass

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the resolved stream transport (rtsp / hls) for visibility."""
        base = super().extra_state_attributes or {}
        if self._stream_transport:
            return {**base, "stream_transport": self._stream_transport}
        return base or None

    async def _fetch_grant(self) -> dict[str, Any]:
        """Fetch a fresh live grant (per-camera or birdseye)."""
        raise NotImplementedError

    async def _async_grant(self) -> dict[str, Any]:
        """Return a cached (or fresh) live grant."""
        now = time.monotonic()
        if self._grant is not None and (now - self._grant_ts) < _GRANT_REUSE_SEC:
            return self._grant
        grant = await self._fetch_grant()
        self._grant = grant
        self._grant_ts = now
        return grant

    async def stream_source(self) -> str | None:
        """Return the best stream URL for HA's stream component / go2rtc.

        RTSP first (direct engine port, same signed token, sub-second latency,
        no HLS segmentation/GOP dependency), HLS fallback when the RTSP port is
        unreachable (HTTP-only reverse proxy deployments).
        """
        try:
            grant = await self._async_grant()
        except YunkanApiError as err:
            _LOGGER.debug("live-grant for %s failed: %s", self._stream_label, err)
            return None
        base_url = self.coordinator.client.base_url
        live_app, live_stream, live_token = pick_live(grant)
        if live_stream and live_token:
            host = urlsplit(base_url).hostname or ""
            rtsp_port = int(grant.get("rtsp_port") or 0) or None
            if await _rtsp_port_reachable(host, rtsp_port or RTSP_PORT_DEFAULT):
                self._stream_transport = "rtsp"
                return build_rtsp_url(base_url, live_app, live_stream, live_token, rtsp_port)
        app, stream, token = pick_stream(grant, "aac_variant")
        if not stream or not token:
            return None
        self._stream_transport = "hls"
        return build_hls_url(base_url, app, stream, token)

    def _async_get_webrtc_client_configuration(self) -> WebRTCClientConfiguration:
        """Return the WebRTC client config (sync hook), adding cached ICE servers."""
        config = super()._async_get_webrtc_client_configuration()
        grant = self._grant
        if grant:
            ice_servers = [
                RTCIceServer(
                    urls=server["urls"],
                    username=server.get("username"),
                    credential=server.get("credential"),
                )
                for server in grant.get("ice_servers", [])
                if server.get("urls")
            ]
            if ice_servers:
                config.configuration.ice_servers.extend(ice_servers)
        return config

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Relay the SDP offer to the server's WHEP endpoint and return the answer."""
        try:
            grant = await self._async_grant()
            app, stream, token = pick_stream(grant, "opus_variant")
            if not stream or not token:
                raise YunkanApiError("no live stream available")
            whep_url = build_whep_url(self.coordinator.client.base_url, app, stream, token)
            answer = await self.coordinator.client.async_whep_offer(whep_url, offer_sdp)
        except YunkanApiError as err:
            _LOGGER.warning("WebRTC offer for %s failed: %s", self._stream_label, err)
            send_message(WebRTCError("webrtc_offer_failed", str(err)))
            return
        send_message(WebRTCAnswer(answer))

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """WHEP is stateless from our side; nothing to tear down locally."""


class YunkanCamera(YunkanCameraEntity, _YunkanStreamCamera):
    """A Yunkan camera exposed to Home Assistant."""

    _attr_name = None  # the device name is the camera name

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the camera entity."""
        YunkanCameraEntity.__init__(self, coordinator, camera_id)
        self._init_stream()
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_camera"
        self._stream_label = camera_id

    @property
    def is_on(self) -> bool:
        """Return whether the camera is enabled on the server."""
        return bool(self._camera.get("enabled", True))

    async def _fetch_grant(self) -> dict[str, Any]:
        """Sign a per-camera live grant."""
        return await self.coordinator.client.async_live_grant(self._camera_id)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the latest snapshot JPEG."""
        return await self.coordinator.client.async_snapshot(self._camera_id)


class YunkanBirdseyeCamera(CoordinatorEntity[YunkanCoordinator], _YunkanStreamCamera):
    """The birdseye overview stream (all detection cameras tiled)."""

    _attr_has_entity_name = True
    _attr_translation_key = "birdseye"

    def __init__(self, coordinator: YunkanCoordinator) -> None:
        """Initialise the birdseye camera."""
        CoordinatorEntity.__init__(self, coordinator)
        self._init_stream()
        self._entry_id = coordinator.entry.entry_id
        self._attr_unique_id = f"{self._entry_id}_{BIRDSEYE_CAMERA_ID}"
        self._stream_label = BIRDSEYE_CAMERA_ID

    @property
    def device_info(self):
        """Attach the birdseye camera to the server (hub) device."""
        return server_device_info(
            self._entry_id, self.coordinator.client.base_url, self.coordinator.data.version
        )

    async def _fetch_grant(self) -> dict[str, Any]:
        """Sign a birdseye live grant."""
        return await self.coordinator.client.async_birdseye_grant()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Birdseye has no still endpoint; HA derives a preview from the stream."""
        return None
