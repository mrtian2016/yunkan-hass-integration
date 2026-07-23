"""HTTP proxy views for the Yunkan integration.

These views let Home Assistant fetch snapshots and recordings from the Yunkan
server using HA's own authentication, so no Yunkan token or internal URL is ever
handed to a browser, cast device or notification. media_source signs these paths
so external players can play a clip for a limited time.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_STREAM_CHUNK = 64 * 1024
_COPIED_HEADERS = (
    "Content-Type",
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
    "Cache-Control",
    "Content-Disposition",
)

# URL builders shared with media_source.
URL_RECORDING = "/api/yunkan/{entry_id}/recording/{recording_id}"
URL_EVENT_SNAPSHOT = "/api/yunkan/{entry_id}/event_snapshot"
URL_CAMERA_SNAPSHOT = "/api/yunkan/{entry_id}/snapshot/{camera_id}"


@callback
def async_register_views(hass: HomeAssistant) -> None:
    """Register the proxy views once for the whole integration."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("views_registered"):
        return
    hass.http.register_view(YunkanCameraSnapshotView())
    hass.http.register_view(YunkanEventSnapshotView())
    hass.http.register_view(YunkanRecordingView())
    domain_data["views_registered"] = True


def _get_coordinator(hass: HomeAssistant, entry_id: str):
    """Return the coordinator for a loaded Yunkan entry id, or None."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or entry.state.name != "LOADED":
        return None
    return getattr(entry, "runtime_data", None)


async def _proxy_bytes(data: bytes | None) -> web.Response:
    """Return a JPEG response for pre-read bytes."""
    if data is None:
        return web.Response(status=404)
    return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "no-store"})


class YunkanCameraSnapshotView(HomeAssistantView):
    """Serve a live camera snapshot through HA auth."""

    url = URL_CAMERA_SNAPSHOT
    name = "api:yunkan:snapshot"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, camera_id: str
    ) -> web.Response:
        """Return the current camera snapshot."""
        hass: HomeAssistant = request.app["hass"]
        coordinator = _get_coordinator(hass, entry_id)
        # Scope to cameras this integration actually exposes (enabled, non
        # archived). Without this, any authenticated HA user could borrow the
        # integration's backend token to snapshot a hidden camera by id.
        if coordinator is None or camera_id not in coordinator.data.cameras:
            return web.Response(status=404)
        data = await coordinator.client.async_snapshot(camera_id)
        return await _proxy_bytes(data)


class YunkanEventSnapshotView(HomeAssistantView):
    """Serve an event snapshot (optionally annotated) through HA auth."""

    url = URL_EVENT_SNAPSHOT
    name = "api:yunkan:event_snapshot"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        """Return the event snapshot referenced by the ``path`` query param."""
        hass: HomeAssistant = request.app["hass"]
        coordinator = _get_coordinator(hass, entry_id)
        if coordinator is None:
            return web.Response(status=404)
        snapshot_path = request.query.get("path")
        # Defence in depth: the backend already blocks traversal, but only ever
        # proxy well-formed event snapshot paths.
        if (
            not snapshot_path
            or not snapshot_path.startswith("events/")
            or ".." in snapshot_path
        ):
            return web.Response(status=400)
        rel = "/api/events/snapshot?" + urlencode({"path": snapshot_path})
        event_id = request.query.get("event_id")
        width = request.query.get("w")
        data = await coordinator.client.async_event_snapshot(
            rel,
            width=int(width) if width and width.isdigit() else None,
            annotate_event_id=int(event_id) if event_id and event_id.isdigit() else None,
        )
        return await _proxy_bytes(data)


class YunkanRecordingView(HomeAssistantView):
    """Stream a recording segment (with Range support) through HA auth."""

    url = URL_RECORDING
    name = "api:yunkan:recording"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, recording_id: str
    ) -> web.StreamResponse:
        """Proxy a recording MP4, forwarding Range for seeking."""
        hass: HomeAssistant = request.app["hass"]
        coordinator = _get_coordinator(hass, entry_id)
        if coordinator is None or not recording_id.isdigit():
            return web.Response(status=404)
        client = coordinator.client

        try:
            info = await client.async_recording_url(int(recording_id))
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("recording %s resolve failed: %s", recording_id, err)
            return web.Response(status=404)

        # Scope to a camera this integration exposes: a recording belonging to a
        # hidden camera must not be reachable by guessing its (sequential) id.
        rec = info.get("recording") if isinstance(info, dict) else None
        rec_camera = rec.get("camera_id") if isinstance(rec, dict) else None
        if rec_camera is not None and rec_camera not in coordinator.data.cameras:
            return web.Response(status=404)

        req_headers: dict[str, str] = {}
        if (range_header := request.headers.get("Range")) is not None:
            req_headers["Range"] = range_header

        is_local = bool(info.get("is_local", True))
        if is_local:
            upstream_url = f"/api/recordings/{int(recording_id)}/download"
            use_auth = True
        else:
            upstream_url = info.get("url")
            use_auth = False
            if info.get("user_agent"):
                req_headers["User-Agent"] = info["user_agent"]
        if not upstream_url:
            return web.Response(status=404)

        upstream = await client.async_open_stream(
            upstream_url, headers=req_headers, use_auth=use_auth
        )
        try:
            if upstream.status not in (200, 206):
                return web.Response(status=upstream.status)
            headers = {
                key: upstream.headers[key]
                for key in _COPIED_HEADERS
                if key in upstream.headers
            }
            headers.setdefault("Content-Type", "video/mp4")
            response = web.StreamResponse(status=upstream.status, headers=headers)
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(_STREAM_CHUNK):
                await response.write(chunk)
            await response.write_eof()
            return response
        finally:
            upstream.release()
