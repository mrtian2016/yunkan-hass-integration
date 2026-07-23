"""Server-Sent Events client for the Yunkan real-time event stream.

Home Assistant's ``EventSource`` equivalent: a resilient background task that
keeps a long-lived ``GET /api/events/stream`` connection open and forwards each
decoded detection event to a callback. Because the stream ticket is a 60-second
JWT, a fresh ticket is fetched on every (re)connect. Reconnects use exponential
backoff so a bounced backend does not hammer the server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
import time

import aiohttp

from homeassistant.core import HomeAssistant

from .api import YunkanApiClient, YunkanApiError

_LOGGER = logging.getLogger(__name__)

_BACKOFF_START = 2.0
_BACKOFF_MAX = 60.0
# The backend sends a ": keepalive" comment every 15s; give it generous slack
# before assuming the socket is wedged and forcing a reconnect.
_READ_TIMEOUT = 45.0

EventCallback = Callable[[dict], Awaitable[None]]


class YunkanSSEClient:
    """Consume the Yunkan SSE event stream in the background."""

    def __init__(
        self, hass: HomeAssistant, client: YunkanApiClient, on_event: EventCallback
    ) -> None:
        """Store the REST client and the per-event async callback."""
        self._hass = hass
        self._client = client
        self._on_event = on_event
        self._task: asyncio.Task | None = None
        self._closing = False
        self._connected = False
        self._last_message: float = 0.0

    @property
    def connected(self) -> bool:
        """Return whether the stream is currently established."""
        return self._connected

    @property
    def last_message_age(self) -> float | None:
        """Return seconds since the last decoded frame, or ``None`` if never."""
        if not self._last_message:
            return None
        return time.monotonic() - self._last_message

    def start(self) -> None:
        """Start the background reconnect loop."""
        if self._task is None or self._task.done():
            self._closing = False
            self._task = self._hass.async_create_background_task(
                self._run(), name="yunkan_sse"
            )

    async def stop(self) -> None:
        """Cancel the background loop and wait for it to finish."""
        self._closing = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._connected = False

    async def _run(self) -> None:
        """Reconnect loop with exponential backoff."""
        backoff = _BACKOFF_START
        while not self._closing:
            try:
                await self._connect_once()
                backoff = _BACKOFF_START
            except asyncio.CancelledError:
                raise
            except (YunkanApiError, aiohttp.ClientError, TimeoutError, OSError) as err:
                self._connected = False
                _LOGGER.debug("SSE connection dropped: %s; retrying in %.0fs", err, backoff)
            except RuntimeError as err:
                # e.g. "Session is closed" during shutdown — stop, don't spin.
                self._connected = False
                _LOGGER.debug("SSE loop stopping: %s", err)
                break
            except Exception:  # noqa: BLE001 - background task must never die silently
                self._connected = False
                _LOGGER.exception("Unexpected error in SSE loop; retrying in %.0fs", backoff)
            if self._closing:
                break
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _connect_once(self) -> None:
        """Open one stream and pump frames until it closes."""
        ticket = await self._client.async_sse_ticket()  # also refreshes the token
        url = self._client.sse_stream_url(ticket)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=_READ_TIMEOUT)
        # The ticket query authenticates the stream, but a reverse proxy may route
        # header-less /api requests away from the API, so send the bearer token too
        # (an aiohttp client, unlike a browser EventSource, can set headers).
        headers = (
            {"Authorization": f"Bearer {self._client.token}"} if self._client.token else {}
        )
        async with self._client._session.get(  # noqa: SLF001
            url, headers=headers, timeout=timeout
        ) as resp:
            if resp.status != 200:
                raise YunkanApiError(f"SSE stream returned HTTP {resp.status}")
            self._connected = True
            self._last_message = time.monotonic()
            _LOGGER.debug("SSE stream connected")
            data_lines: list[str] = []
            async for raw in resp.content:
                if self._closing:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\n").rstrip("\r")
                if line == "":
                    # Blank line terminates an event.
                    await self._dispatch(data_lines)
                    data_lines = []
                    continue
                if line.startswith(":"):
                    # Comment / keepalive.
                    self._last_message = time.monotonic()
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip(" "))
                # "event:" / "id:" fields are ignored; business frames are unnamed.
        # Stream ended cleanly (server closed it); clear connected so it doesn't
        # read stale-True through the reconnect backoff.
        self._connected = False

    async def _dispatch(self, data_lines: list[str]) -> None:
        """Decode a completed SSE frame and forward it."""
        if not data_lines:
            return
        self._last_message = time.monotonic()
        payload = "\n".join(data_lines)
        if not payload or payload == "{}":
            # The initial "hello" frame carries an empty object.
            return
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            _LOGGER.debug("Ignoring non-JSON SSE frame: %s", payload[:120])
            return
        if not isinstance(event, dict):
            return
        try:
            await self._on_event(event)
        except Exception:  # noqa: BLE001 - a bad handler must not kill the stream
            _LOGGER.exception("SSE event handler raised")
