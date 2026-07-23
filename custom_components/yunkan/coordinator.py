"""Data coordinator for the Yunkan integration.

One coordinator per config entry polls the authoritative slow-moving state
(camera list, license tier, server version, detection running status) and owns
the SSE client that pushes real-time detection events. Entities that react to
events subscribe to a dispatcher signal instead of the coordinator refresh, so a
single event does not trigger a full poll.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    YunkanApiClient,
    YunkanAuthError,
    YunkanConnectionError,
    YunkanSetupRequiredError,
)
from .const import (
    DETECTION_FEATURE_SWITCHES,
    DOMAIN,
    GLOBAL_DETECTION_SWITCHES,
    GLOBAL_FEATURE_SETTING_KEY,
)
from .sse import YunkanSSEClient
from .tracks import YunkanTracksClient

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)


def signal_event(entry_id: str) -> str:
    """Dispatcher signal fired for every real-time detection event."""
    return f"{DOMAIN}_{entry_id}_event"


def signal_tracks(entry_id: str) -> str:
    """Dispatcher signal fired when a camera's live occupancy changes."""
    return f"{DOMAIN}_{entry_id}_tracks"


@dataclass
class YunkanData:
    """Snapshot of the polled backend state."""

    cameras: dict[str, dict[str, Any]] = field(default_factory=dict)
    license: dict[str, Any] = field(default_factory=dict)
    version: dict[str, Any] = field(default_factory=dict)
    # Server-wide effective detection settings keyed by dot-path
    # ("detection.<feature>.enabled" -> bool). Empty when unreadable (non-admin).
    global_settings: dict[str, Any] = field(default_factory=dict)

    @property
    def is_pro(self) -> bool:
        """Return whether the server is on the Pro tier."""
        return bool(self.license.get("is_pro"))

    def global_setting_bool(self, setting_key: str, default: bool) -> bool:
        """Return a server-wide boolean setting by dot-path, or ``default``.

        ``default`` is used when the setting couldn't be read (non-admin) or
        isn't a boolean.
        """
        value = self.global_settings.get(setting_key)
        return value if isinstance(value, bool) else default

    def global_feature_enabled(self, feature: str) -> bool:
        """Return the resolved server-wide enable state of a detection feature.

        Falls back to the feature's shipped default when the server-wide
        settings couldn't be read (e.g. a non-admin account).
        """
        return self.global_setting_bool(
            GLOBAL_FEATURE_SETTING_KEY.format(feature=feature),
            DETECTION_FEATURE_SWITCHES[feature][3],
        )

    @property
    def global_settings_known(self) -> bool:
        """Return whether server-wide detection settings were successfully read."""
        return bool(self.global_settings)


class YunkanCoordinator(DataUpdateCoordinator[YunkanData]):
    """Coordinate polling and event fan-out for a Yunkan server."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: YunkanApiClient
    ) -> None:
        """Initialise the coordinator and its SSE client."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            config_entry=entry,
        )
        self.client = client
        self.entry = entry
        # Most recent event object per camera (fed by SSE, read by image/sensor).
        self.latest_events: dict[str, dict[str, Any]] = {}
        # Per-category live object counts per camera (fed by live-tracks);
        # occupancy = count > 0.
        self.tracks_counts: dict[str, dict[str, int]] = {}
        self.sse = YunkanSSEClient(hass, client, self._handle_event)
        self._tracks_clients: dict[str, YunkanTracksClient] = {}
        self._tracks_started = False
        self._unsub_reconcile: CALLBACK_TYPE | None = None
        # Serialise read-modify-write of a camera's detection_overrides so
        # concurrent feature-switch toggles don't clobber each other.
        self._overrides_locks: dict[str, asyncio.Lock] = {}
        # Serialise the master detection toggle: it hits a *flip* endpoint, so two
        # concurrent turn_on calls (or a double-tap) must not double-flip.
        self._detection_locks: dict[str, asyncio.Lock] = {}

    def overrides_lock(self, camera_id: str) -> asyncio.Lock:
        """Return the per-camera lock guarding detection_overrides updates."""
        return self._overrides_locks.setdefault(camera_id, asyncio.Lock())

    def detection_lock(self, camera_id: str) -> asyncio.Lock:
        """Return the per-camera lock guarding the master detection toggle."""
        return self._detection_locks.setdefault(camera_id, asyncio.Lock())

    async def _async_update_data(self) -> YunkanData:
        """Fetch the current camera list, license, version and detection state."""
        try:
            cameras = await self.client.async_list_cameras()
        except YunkanSetupRequiredError as err:
            raise UpdateFailed("Yunkan server is still in setup mode") from err
        except YunkanAuthError as err:
            # Surface as a re-auth flow rather than a transient failure.
            raise ConfigEntryAuthFailed(str(err)) from err
        except YunkanConnectionError as err:
            raise UpdateFailed(str(err)) from err

        # Only surface enabled, non-archived cameras as entities.
        data = YunkanData(
            cameras={
                cam["id"]: cam
                for cam in cameras
                if cam.get("id")
                and cam.get("enabled", True)
                and not cam.get("archived_at")
            }
        )

        # License and version are best-effort: a hiccup must not drop the cameras.
        try:
            data.license = await self.client.async_license()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("license poll failed: %s", err)
            data.license = self.data.license if self.data else {}

        try:
            version = await self.client.async_version()
            data.version = version if isinstance(version, dict) else {}
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("version poll failed: %s", err)
            data.version = self.data.version if self.data else {}

        # Server-wide detection settings are admin-only and best-effort: a
        # non-admin account (403) or any hiccup just means "global state unknown"
        # and switches fall back to shipped defaults. Keep only the detection
        # feature keys to avoid retaining the full settings blob.
        try:
            values = await self.client.async_get_settings()
            data.global_settings = {
                key: values[key]
                for (key, _tk, _icon, _default) in GLOBAL_DETECTION_SWITCHES.values()
                if key in values
            }
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("settings poll failed (non-admin?): %s", err)
            data.global_settings = self.data.global_settings if self.data else {}

        return data

    @callback
    def _handle_camera_set_change(self) -> None:
        """Reconcile live-tracks clients after the camera set changes."""
        if self._tracks_started:
            self._reconcile_tracks()

    async def async_start_stream(self) -> None:
        """Start the SSE event stream and per-camera live-tracks streams."""
        self.sse.start()
        self._tracks_started = True
        self._reconcile_tracks()
        # Reconcile tracks clients whenever the polled camera set changes.
        self._unsub_reconcile = self.async_add_listener(self._handle_camera_set_change)

    async def async_stop_stream(self) -> None:
        """Stop the SSE event stream and all live-tracks streams."""
        if self._unsub_reconcile is not None:
            self._unsub_reconcile()
            self._unsub_reconcile = None
        await self.sse.stop()
        for tracks in list(self._tracks_clients.values()):
            await tracks.stop()
        self._tracks_clients.clear()

    def _reconcile_tracks(self) -> None:
        """Start/stop live-tracks clients to match the current camera set."""
        current = set(self.data.cameras)
        for camera_id in current - set(self._tracks_clients):
            tracks = YunkanTracksClient(
                self.hass, self.client, camera_id, self._handle_tracks
            )
            self._tracks_clients[camera_id] = tracks
            tracks.start()
        for camera_id in set(self._tracks_clients) - current:
            client = self._tracks_clients.pop(camera_id)
            self.tracks_counts.pop(camera_id, None)
            # Drop the retained latest event too, so a camera re-added with the
            # same id doesn't seed its image / last-event entity from stale data.
            self.latest_events.pop(camera_id, None)
            self.hass.async_create_task(client.stop())

    async def _handle_tracks(self, camera_id: str, counts: dict[str, int]) -> None:
        """Store live object counts for a camera and fan them out to entities."""
        self.tracks_counts[camera_id] = counts
        async_dispatcher_send(self.hass, signal_tracks(self.entry.entry_id), camera_id)

    async def _handle_event(self, event: dict[str, Any]) -> None:
        """Store the latest event per camera and fan it out to entities."""
        camera_id = event.get("camera_id")
        if not camera_id:
            return
        # Skip export-progress / non-detection frames without a camera event type.
        if not event.get("event_type"):
            return
        self.latest_events[camera_id] = event
        self._dispatch_event(event)

    @callback
    def _dispatch_event(self, event: dict[str, Any]) -> None:
        """Send the event to all subscribed entities."""
        async_dispatcher_send(self.hass, signal_event(self.entry.entry_id), event)
