"""Per-camera live detection tracks client for occupancy.

Subscribes to a camera's ``/live-tracks`` SSE stream (published by the detection
process while detection runs) and derives the set of object categories currently
present, so occupancy sensors can stay ON while a person/vehicle/animal is in
view — not just pulse on an event. The boxes themselves are ignored (Home
Assistant cannot draw them on the live feed); only the presence data is used.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
import json
import logging
import time

import aiohttp

from homeassistant.core import HomeAssistant

from .api import YunkanApiClient, YunkanApiError
from .const import EVENT_CATEGORY_MAP, LIVE_TRACKS_STALE, MOMENTARY_CATEGORIES

_LOGGER = logging.getLogger(__name__)

_BACKOFF_START = 2.0
_BACKOFF_MAX = 60.0
_READ_TIMEOUT = 30.0

# Callback: (camera_id, per-category counts)
TracksCallback = Callable[[str, dict[str, int]], Awaitable[None]]


def _count_categories(tracks: list[dict]) -> dict[str, int]:
    """Map live tracks to per-category counts (occupancy = count > 0)."""
    counts: dict[str, int] = {}
    for track in tracks:
        if not isinstance(track, dict):
            continue
        category = EVENT_CATEGORY_MAP.get(track.get("label", ""))
        if category and category not in MOMENTARY_CATEGORIES:
            counts[category] = counts.get(category, 0) + 1
        if track.get("name"):
            counts["face"] = counts.get("face", 0) + 1
        if track.get("is_fall"):
            counts["fall"] = counts.get("fall", 0) + 1
    return counts


class YunkanTracksClient:
    """Maintain live occupancy for a single camera from its tracks stream."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: YunkanApiClient,
        camera_id: str,
        on_update: TracksCallback,
    ) -> None:
        """Store the REST client, camera id and update callback."""
        self._hass = hass
        self._client = client
        self._camera_id = camera_id
        self._on_update = on_update
        self._task: asyncio.Task | None = None
        self._closing = False
        self._current: dict[str, int] = {}

    @property
    def counts(self) -> dict[str, int]:
        """Return the current per-category counts."""
        return self._current

    def start(self) -> None:
        """Start the background reconnect loop."""
        if self._task is None or self._task.done():
            self._closing = False
            self._task = self._hass.async_create_background_task(
                self._run(), name=f"yunkan_tracks_{self._camera_id}"
            )

    async def stop(self) -> None:
        """Cancel the background loop."""
        self._closing = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _emit(self, counts: dict[str, int]) -> None:
        """Forward a count change to the callback."""
        if counts == self._current:
            return
        self._current = counts
        await self._on_update(self._camera_id, counts)

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
                _LOGGER.debug("tracks stream for %s dropped: %s", self._camera_id, err)
            except RuntimeError as err:
                # e.g. "Session is closed" during shutdown — stop, don't spin.
                _LOGGER.debug("tracks stream for %s stopping: %s", self._camera_id, err)
                break
            except Exception:  # noqa: BLE001
                _LOGGER.exception("tracks loop error for %s", self._camera_id)
            if self._closing:
                break
            await self._emit({})
            # Cancellation during the backoff must propagate (do not suppress).
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _connect_once(self) -> None:
        """Open the tracks stream and process frames until it closes."""
        # The endpoint authenticates via an sse-ticket query param; the bearer
        # header is only needed so the reverse proxy routes us to the API.
        ticket = await self._client.async_sse_ticket()
        headers = {"Authorization": f"Bearer {self._client.token}"}
        url = self._client.live_tracks_url(self._camera_id, ticket)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=_READ_TIMEOUT)
        async with self._client._session.get(  # noqa: SLF001
            url, headers=headers, timeout=timeout
        ) as resp:
            if resp.status != 200:
                raise YunkanApiError(f"live-tracks HTTP {resp.status}")
            last_data = time.monotonic()
            async for raw in resp.content:
                if self._closing:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith(":"):
                    # keepalive — clear occupancy if data has gone stale.
                    if time.monotonic() - last_data > LIVE_TRACKS_STALE:
                        await self._emit({})
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[5:].lstrip(" ")
                if not payload or payload == "{}":
                    continue
                try:
                    frame = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if not isinstance(frame, dict) or frame.get("type") != "overlay.tracks":
                    continue
                last_data = time.monotonic()
                await self._emit(_count_categories(frame.get("tracks", [])))
