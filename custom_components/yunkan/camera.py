"""Camera platform for the Yunkan integration.

Each Yunkan camera becomes a native HA camera entity:
* snapshots come from the backend's JPEG snapshot endpoint;
* live view uses HA's native WebRTC path (2024.11+) by relaying the browser's
  SDP offer to the server's WHEP endpoint and returning the answer;
* HLS is offered as a fallback ``stream_source`` when WebRTC can't be reached.

All playback URLs are signed with a short-lived live-grant token minted per
session; nothing but the nginx entry (base URL) is ever exposed.
"""

from __future__ import annotations

import logging
import time
from typing import Any

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
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YunkanConfigEntry
from .api import YunkanApiError
from .coordinator import YunkanCoordinator
from .entity import YunkanCameraEntity
from .urls import build_hls_url, build_whep_url, pick_stream

_LOGGER = logging.getLogger(__name__)

# Reuse a live-grant across the client-config + offer handshake instead of
# minting one per call. Comfortably shorter than the 1800s server TTL.
_GRANT_REUSE_SEC = 600.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YunkanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yunkan cameras from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        YunkanCamera(coordinator, camera_id) for camera_id in coordinator.data.cameras
    )


class YunkanCamera(YunkanCameraEntity, Camera):
    """A Yunkan camera exposed to Home Assistant."""

    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_name = None  # the device name is the camera name

    def __init__(self, coordinator: YunkanCoordinator, camera_id: str) -> None:
        """Initialise the camera entity."""
        YunkanCameraEntity.__init__(self, coordinator, camera_id)
        Camera.__init__(self)
        self._attr_unique_id = f"{self._entry_id}_{camera_id}_camera"
        self._grant: dict[str, Any] | None = None
        self._grant_ts: float = 0.0

    @property
    def is_on(self) -> bool:
        """Return whether the camera is enabled on the server."""
        return bool(self._camera.get("enabled", True))

    @property
    def available(self) -> bool:
        """Camera is available while the poll succeeds and it still exists."""
        return super().available

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the latest snapshot JPEG."""
        return await self.coordinator.client.async_snapshot(self._camera_id)

    async def _async_grant(self) -> dict[str, Any]:
        """Return a cached (or fresh) live-grant for this camera."""
        now = time.monotonic()
        if self._grant is not None and (now - self._grant_ts) < _GRANT_REUSE_SEC:
            return self._grant
        grant = await self.coordinator.client.async_live_grant(self._camera_id)
        self._grant = grant
        self._grant_ts = now
        return grant

    async def stream_source(self) -> str | None:
        """Return an HLS URL for the HA stream component (fallback path)."""
        try:
            grant = await self._async_grant()
        except YunkanApiError as err:
            _LOGGER.debug("live-grant for %s failed: %s", self._camera_id, err)
            return None
        app, stream, token = pick_stream(grant, "aac_variant")
        if not stream or not token:
            return None
        return build_hls_url(self.coordinator.client.base_url, app, stream, token)

    def _async_get_webrtc_client_configuration(self) -> WebRTCClientConfiguration:
        """Return the WebRTC client config (sync hook), adding cached ICE servers.

        Home Assistant calls this synchronously, so any ICE servers must come
        from a live-grant cached by a prior async call rather than a fresh fetch.
        On a LAN the server returns no ICE servers and direct candidates suffice.
        """
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
            _LOGGER.warning("WebRTC offer for %s failed: %s", self._camera_id, err)
            send_message(WebRTCError("webrtc_offer_failed", str(err)))
            return
        send_message(WebRTCAnswer(answer))

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """WHEP is stateless from our side; nothing to tear down locally."""
