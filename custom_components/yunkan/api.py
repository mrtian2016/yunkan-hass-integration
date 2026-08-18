"""REST client for the Yunkan backend.

The client is a thin consumer of the already-public REST/SSE contract exposed by
the Yunkan API process behind its nginx entry (default port 23406). It carries
no business logic: authentication, gating and data live on the server.

Backend conventions handled here:
* success responses are wrapped as ``{"code": 0, "data": ..., "message": ...}``;
* error responses carry the HTTP status and a ``message``/``detail`` string;
* Pro-only endpoints answer ``403`` with ``message`` ``LICENSE_REQUIRED:<status>``;
* the access token is a 30-day HS256 JWT with no refresh token, so a ``401`` is
  recovered by logging in again with the stored credentials.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp
from yarl import URL

from .const import (
    API_BIRDSEYE_GRANT,
    API_CAMERA_SNAPSHOT,
    API_CAMERA_UPDATE,
    API_CAMERAS,
    API_DETECTION_STATUS,
    API_DETECTION_TOGGLE,
    API_EVENT_DETAIL,
    API_EVENTS,
    API_EXPORT,
    API_LICENSE_STATUS,
    API_LIVE_GRANT,
    API_LIVE_TRACKS,
    API_LOGIN,
    API_PTZ_MOVE,
    API_PTZ_PRESET_GOTO,
    API_PTZ_PRESETS,
    API_PTZ_STOP,
    API_SETTINGS_ALL,
    API_SETTINGS_BULK,
    API_TIMELAPSE,
    API_SETUP_STATUS,
    API_SSE_TICKET,
    API_SYSTEM_VERSION,
    API_TTS_BROADCAST,
    LICENSE_REQUIRED_PREFIX,
    RESPONSE_CODE_OK,
)

_LOGGER = logging.getLogger(__name__)

# Endpoints that reply with raw bytes rather than the JSON envelope.
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
_SNAPSHOT_TIMEOUT = aiohttp.ClientTimeout(total=15)


class YunkanApiError(Exception):
    """Base error for the Yunkan client."""


class YunkanConnectionError(YunkanApiError):
    """Raised when the backend cannot be reached."""


class YunkanAuthError(YunkanApiError):
    """Raised when credentials are missing, wrong or the session is invalid."""


class YunkanSetupRequiredError(YunkanApiError):
    """Raised when the backend has not finished its first-run setup wizard."""


class YunkanProRequiredError(YunkanApiError):
    """Raised when a Pro-only endpoint is called on a free-tier server."""

    def __init__(self, status: str) -> None:
        """Store the license status reported by the backend."""
        super().__init__(f"LICENSE_REQUIRED:{status}")
        self.license_status = status


class YunkanForbiddenError(YunkanApiError):
    """Raised on a non-license 403 — the account lacks the required role.

    Kept distinct from the generic :class:`YunkanApiError` so callers can tell
    "this account may not do that" (definitive, act on it) apart from "the
    server / a proxy hiccuped" (transient, retry) — see panel.py.
    """


def normalize_base_url(raw: str) -> str:
    """Normalise a user-entered base URL (add scheme, strip trailing slash).

    Scheme detection is case-insensitive: addresses pasted from elsewhere often
    carry an upper-case ``HTTPS://``, which must not be double-prefixed.
    """
    raw = raw.strip()
    if not raw.lower().startswith(("http://", "https://")):
        raw = f"http://{raw}"
    return raw.rstrip("/")


class YunkanApiClient:
    """Async REST client bound to a single Yunkan server."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        username: str,
        password: str,
    ) -> None:
        """Initialise the client with an aiohttp session and credentials."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._token: str | None = None
        self._auth_lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        """Return the configured base URL (nginx entry)."""
        return self._base_url

    @property
    def token(self) -> str | None:
        """Return the current access token, if logged in."""
        return self._token

    def abs_url(self, path_or_url: str) -> str:
        """Return an absolute URL for a backend path or a relative URL."""
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        if not path_or_url.startswith("/"):
            path_or_url = "/" + path_or_url
        return f"{self._base_url}{path_or_url}"

    # ------------------------------------------------------------------ auth

    async def async_login(self) -> dict[str, Any]:
        """Log in and cache the access token. Returns the user object."""
        try:
            async with self._session.post(
                self.abs_url(API_LOGIN),
                json={"username": self._username, "password": self._password},
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                body = await _read_json(resp)
                if resp.status == 200 and _envelope_ok(body):
                    data = body["data"]
                    self._token = data["access_token"]
                    return data.get("user", {})
                message = _error_message(body)
                if resp.status == 412 or message == "SETUP_REQUIRED":
                    raise YunkanSetupRequiredError(message or "SETUP_REQUIRED")
                raise YunkanAuthError(message or f"HTTP {resp.status}")
        except aiohttp.ClientError as err:
            raise YunkanConnectionError(str(err)) from err
        except TimeoutError as err:
            raise YunkanConnectionError("login timed out") from err

    async def _ensure_token(self) -> None:
        """Log in if no token is cached yet."""
        if self._token is None:
            async with self._auth_lock:
                if self._token is None:
                    await self.async_login()

    async def _reauth(self) -> None:
        """Re-login once under the lock, coalescing concurrent 401s.

        Uses a double-checked pattern so N concurrent callers that all saw the
        same stale token trigger a single re-login rather than N.
        """
        stale = self._token
        async with self._auth_lock:
            if self._token == stale:
                self._token = None
                await self.async_login()

    # -------------------------------------------------------------- requests

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        auth: bool = True,
        _retry: bool = True,
    ) -> Any:
        """Perform a JSON request and return the ``data`` payload."""
        if auth:
            await self._ensure_token()
        headers = {"Authorization": f"Bearer {self._token}"} if auth and self._token else {}
        try:
            async with self._session.request(
                method,
                self.abs_url(path),
                params=params,
                json=json,
                headers=headers,
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                body = await _read_json(resp)
                if resp.status == 200 and _envelope_ok(body):
                    return body.get("data")
                await self._raise_for_error(resp.status, body, method, path, auth, _retry)
                # _raise_for_error only returns when a re-auth retry is warranted.
                return await self._request(
                    method, path, params=params, json=json, auth=auth, _retry=False
                )
        except aiohttp.ClientError as err:
            raise YunkanConnectionError(str(err)) from err
        except TimeoutError as err:
            raise YunkanConnectionError(f"{method} {path} timed out") from err

    async def _raise_for_error(
        self,
        status: int,
        body: Any,
        method: str,
        path: str,
        auth: bool,
        retry: bool,
    ) -> None:
        """Translate an error response into the right exception (or trigger a retry)."""
        message = _error_message(body)
        if status == 401 and auth and retry:
            # 30-day JWT expired or invalidated: log in again and let the caller retry.
            await self._reauth()
            return
        if status == 401:
            raise YunkanAuthError(message or "unauthorized")
        if status == 403 and message and message.startswith(LICENSE_REQUIRED_PREFIX):
            raise YunkanProRequiredError(message[len(LICENSE_REQUIRED_PREFIX) :])
        if status == 403:
            raise YunkanForbiddenError(message or f"{method} {path} -> HTTP 403")
        if status == 503 and message in ("SYSTEM_IN_SETUP", "SETUP_REQUIRED"):
            raise YunkanSetupRequiredError(message)
        raise YunkanApiError(f"{method} {path} -> HTTP {status}: {message or 'error'}")

    # ------------------------------------------------------------ meta / gate

    async def async_setup_status(self) -> dict[str, Any]:
        """Return ``{needs_setup: bool}`` (no auth required)."""
        try:
            async with self._session.get(
                self.abs_url(API_SETUP_STATUS), timeout=_REQUEST_TIMEOUT
            ) as resp:
                body = await _read_json(resp)
                if resp.status == 200 and _envelope_ok(body):
                    return body["data"]
                raise YunkanApiError(_error_message(body) or f"HTTP {resp.status}")
        except aiohttp.ClientError as err:
            raise YunkanConnectionError(str(err)) from err
        except TimeoutError as err:
            raise YunkanConnectionError("setup-status timed out") from err

    async def async_license(self) -> dict[str, Any]:
        """Return the license status object (no auth required)."""
        try:
            async with self._session.get(
                self.abs_url(API_LICENSE_STATUS), timeout=_REQUEST_TIMEOUT
            ) as resp:
                body = await _read_json(resp)
                if resp.status == 200 and _envelope_ok(body):
                    return body["data"]
                return {"status": "unlicensed", "is_pro": False, "tier": "free"}
        except (aiohttp.ClientError, TimeoutError):
            return {"status": "unknown", "is_pro": False, "tier": "free"}

    async def async_version(self) -> dict[str, Any]:
        """Return the system version payload."""
        return await self._request("GET", API_SYSTEM_VERSION)

    # -------------------------------------------------------------- cameras

    async def async_list_cameras(self) -> list[dict[str, Any]]:
        """Return the list of cameras visible to the current user."""
        data = await self._request("GET", API_CAMERAS)
        return data if isinstance(data, list) else []

    async def async_snapshot(
        self, camera_id: str, *, force: bool = False, _retry: bool = True
    ) -> bytes | None:
        """Return the latest camera snapshot JPEG, or ``None`` if unavailable."""
        await self._ensure_token()
        path = API_CAMERA_SNAPSHOT.format(camera_id=quote(camera_id, safe=""))
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with self._session.get(
                self.abs_url(path),
                params={"force": "true"} if force else None,
                headers=headers,
                timeout=_SNAPSHOT_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    return await resp.read()
                if resp.status == 401 and _retry:
                    await self._reauth()
                    return await self.async_snapshot(camera_id, force=force, _retry=False)
                return None
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("snapshot fetch for %s failed: %s", camera_id, err)
            return None

    async def async_live_grant(self, camera_id: str) -> dict[str, Any]:
        """Sign a short-lived live playback grant for a camera."""
        path = API_LIVE_GRANT.format(camera_id=quote(camera_id, safe=""))
        return await self._request("POST", path)

    async def async_birdseye_grant(self) -> dict[str, Any]:
        """Sign a live grant for the birdseye overview stream (admin + enabled)."""
        return await self._request("POST", API_BIRDSEYE_GRANT)

    async def async_update_camera(self, camera_id: str, **fields: Any) -> dict[str, Any]:
        """Update camera fields (PUT, admin) — e.g. record_mode / detection_enabled."""
        path = API_CAMERA_UPDATE.format(camera_id=quote(camera_id, safe=""))
        return await self._request("PUT", path, json=fields)

    async def async_get_settings(self) -> dict[str, Any]:
        """Return the server's effective settings values keyed by dot-path (admin).

        Wraps GET /api/settings/all, returning just the ``values`` map (dot-path
        -> current effective value). Requires an admin account; non-admins get a
        403, which the caller treats as "global settings unknown".
        """
        data = await self._request("GET", API_SETTINGS_ALL)
        values = data.get("values") if isinstance(data, dict) else None
        return values if isinstance(values, dict) else {}

    async def async_update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Bulk-update server settings by dot-path (admin).

        ``updates`` maps whitelisted dot-paths to values, e.g.
        ``{"detection.face.enabled": True}``. The backend validates against its
        own allow-list and silently drops unknown keys.
        """
        return await self._request("PUT", API_SETTINGS_BULK, json=updates)

    async def async_create_export(
        self, camera_id: str, start_ms: int, end_ms: int
    ) -> dict[str, Any]:
        """Queue a recording export job for a time range (epoch milliseconds)."""
        return await self._request(
            "POST",
            API_EXPORT,
            json={"camera_id": camera_id, "start_ms": start_ms, "end_ms": end_ms},
        )

    def live_tracks_url(self, camera_id: str, ticket: str) -> str:
        """Build the per-camera live detection tracks SSE URL (ticket-authed)."""
        path = API_LIVE_TRACKS.format(camera_id=quote(camera_id, safe=""))
        return self.abs_url(f"{path}?{urlencode({'ticket': ticket})}")

    async def async_whep_offer(self, whep_url: str, offer_sdp: str) -> str:
        """POST an SDP offer to a WHEP endpoint and return the SDP answer."""
        try:
            async with self._session.post(
                whep_url,
                data=offer_sdp,
                headers={"Content-Type": "application/sdp"},
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                if resp.status in (200, 201):
                    return await resp.text()
                detail = await resp.text()
                raise YunkanApiError(f"WHEP offer failed: HTTP {resp.status} {detail[:200]}")
        except aiohttp.ClientError as err:
            raise YunkanConnectionError(str(err)) from err
        except TimeoutError as err:
            raise YunkanConnectionError("WHEP offer timed out") from err

    # ------------------------------------------------------------- detection

    async def async_detection_status(self) -> dict[str, Any]:
        """Return the aggregate detection status (running counts per camera)."""
        try:
            return await self._request("GET", API_DETECTION_STATUS)
        except YunkanProRequiredError:
            return {"total": 0, "running": 0, "cameras": []}

    async def async_toggle_detection(self, camera_id: str) -> dict[str, Any]:
        """Flip the detection enabled flag for a camera (Pro + admin)."""
        path = API_DETECTION_TOGGLE.format(camera_id=quote(camera_id, safe=""))
        return await self._request("POST", path)

    # ---------------------------------------------------------------- events

    async def async_list_events(
        self,
        *,
        camera: str | None = None,
        event_type: str | None = None,
        date: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List events with optional filters. Returns the full ``data`` payload."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if camera:
            params["camera"] = camera
        if event_type:
            params["type"] = event_type
        if date:
            params["date"] = date
        return await self._request("GET", API_EVENTS, params=params)

    async def async_get_event(self, event_id: int) -> dict[str, Any]:
        """Return a single event by id."""
        return await self._request("GET", API_EVENT_DETAIL.format(event_id=event_id))

    async def async_event_snapshot(
        self,
        snapshot_url: str,
        *,
        width: int | None = None,
        annotate_event_id: int | None = None,
        crop_event_id: int | None = None,
        _retry: bool = True,
    ) -> bytes | None:
        """Fetch an event snapshot JPEG via the Bearer-authenticated snapshot endpoint.

        ``snapshot_url`` is the relative URL carried on the event
        (``/api/events/snapshot?path=...``). Extra query params are merged:
        ``annotate_event_id`` draws detection boxes server-side and
        ``crop_event_id`` crops around the detected object (both need the
        event id; pass the same id to combine them).
        """
        await self._ensure_token()
        url = URL(self.abs_url(snapshot_url))
        query: dict[str, Any] = {}
        if width:
            query["w"] = width
        if annotate_event_id is not None:
            query["annotate"] = 1
            query["event_id"] = annotate_event_id
        if crop_event_id is not None:
            query["crop"] = 1
            query["event_id"] = crop_event_id
        if query:
            url = url.update_query(query)
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with self._session.get(
                url, headers=headers, timeout=_SNAPSHOT_TIMEOUT
            ) as resp:
                if resp.status == 200:
                    return await resp.read()
                if resp.status == 401 and _retry:
                    await self._reauth()
                    return await self.async_event_snapshot(
                        snapshot_url,
                        width=width,
                        annotate_event_id=annotate_event_id,
                        crop_event_id=crop_event_id,
                        _retry=False,
                    )
                return None
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("event snapshot fetch failed: %s", err)
            return None

    # ------------------------------------------------------- timeline / vod

    async def async_timeline(self, camera_id: str, date: str) -> dict[str, Any]:
        """Return the recordings + events timeline for a camera on a given day."""
        path = f"/api/cameras/{quote(camera_id, safe='')}/timeline"
        return await self._request("GET", path, params={"date": date})

    async def async_recording_dates(self, camera_id: str) -> list[str]:
        """Return the list of dates that have recordings for a camera."""
        data = await self._request(
            "GET", "/api/recordings/dates", params={"camera": camera_id}
        )
        if isinstance(data, dict):
            dates = data.get("dates", [])
        else:
            dates = data or []
        return [str(d) for d in dates]

    async def async_recording_url(self, recording_id: int) -> dict[str, Any]:
        """Resolve a playable URL for a recording segment."""
        return await self._request("GET", f"/api/recordings/{recording_id}/url")

    async def async_open_stream(
        self, path_or_url: str, *, headers: dict[str, str] | None = None, use_auth: bool = True
    ) -> aiohttp.ClientResponse:
        """Open a streaming GET response for proxying media through HA.

        The caller owns the returned response and must release/close it. Used by
        the HA-authenticated proxy views so no Yunkan token ever reaches the
        client's browser or a cast device.
        """
        req_headers = dict(headers or {})
        if use_auth:
            await self._ensure_token()
            req_headers["Authorization"] = f"Bearer {self._token}"
        resp = await self._session.get(
            self.abs_url(path_or_url),
            headers=req_headers,
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60),
        )
        return resp

    # ------------------------------------------------------------------- ptz

    async def async_ptz(self, camera_id: str, direction: str, speed: float) -> None:
        """Issue a continuous PTZ move command."""
        path = API_PTZ_MOVE.format(camera_id=quote(camera_id, safe=""))
        await self._request("POST", path, json={"direction": direction, "speed": speed})

    async def async_ptz_stop(self, camera_id: str) -> None:
        """Stop PTZ movement."""
        path = API_PTZ_STOP.format(camera_id=quote(camera_id, safe=""))
        await self._request("POST", path)

    async def async_ptz_goto_preset(
        self, camera_id: str, preset_token: str, speed: float
    ) -> None:
        """Move the camera to a stored preset position."""
        path = API_PTZ_PRESET_GOTO.format(
            camera_id=quote(camera_id, safe=""),
            preset_token=quote(preset_token, safe=""),
        )
        await self._request("POST", path, json={"speed": speed})

    async def async_ptz_presets(self, camera_id: str) -> list[dict[str, Any]]:
        """List a camera's stored PTZ presets."""
        path = API_PTZ_PRESETS.format(camera_id=quote(camera_id, safe=""))
        data = await self._request("GET", path)
        return data if isinstance(data, list) else []

    async def async_create_timelapse(
        self, camera_id: str, start_ms: int, end_ms: int, speed: int
    ) -> dict[str, Any]:
        """Queue a timelapse export job (speed = fixed multiplier, >= 2)."""
        return await self._request(
            "POST",
            API_TIMELAPSE,
            json={
                "camera_id": camera_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "speed": speed,
            },
        )

    # --------------------------------------------------------------- talkback

    async def async_tts_broadcast(
        self,
        camera_id: str,
        text: str,
        *,
        voice: str | None = None,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> dict[str, Any]:
        """Synthesise text and play it on a camera speaker (Pro + admin)."""
        path = API_TTS_BROADCAST.format(camera_id=quote(camera_id, safe=""))
        payload: dict[str, Any] = {"text": text}
        if voice:
            payload["voice"] = voice
        if rate:
            payload["rate"] = rate
        if pitch:
            payload["pitch"] = pitch
        return await self._request("POST", path, json=payload)

    # --------------------------------------------------------------------- sse

    async def async_sse_ticket(self) -> str:
        """Fetch a short-lived SSE ticket for the event stream."""
        data = await self._request("POST", API_SSE_TICKET)
        return data["ticket"]

    def sse_stream_url(self, ticket: str) -> str:
        """Build the SSE stream URL for the given ticket."""
        return self.abs_url(f"{API_EVENTS}/stream?{urlencode({'ticket': ticket})}")


def _envelope_ok(body: Any) -> bool:
    """Return True when a JSON body is a successful ``{code: 0, ...}`` envelope."""
    return isinstance(body, dict) and body.get("code") == RESPONSE_CODE_OK


def _error_message(body: Any) -> str | None:
    """Extract a human-readable message from an error body (envelope or FastAPI)."""
    if isinstance(body, dict):
        msg = body.get("message") or body.get("detail")
        if isinstance(msg, str):
            return msg
        if msg is not None:
            return str(msg)
    return None


async def _read_json(resp: aiohttp.ClientResponse) -> Any:
    """Read a response body as JSON, tolerating non-JSON error pages."""
    try:
        return await resp.json(content_type=None)
    except (aiohttp.ClientError, ValueError):
        return None
