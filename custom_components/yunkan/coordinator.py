"""Data coordinator for the Yunkan integration.

One coordinator per config entry polls the authoritative slow-moving state
(camera list, license tier, server version, detection running status) and owns
the SSE client that pushes real-time detection events. Entities that react to
events subscribe to a dispatcher signal instead of the coordinator refresh, so a
single event does not trigger a full poll.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    YunkanApiClient,
    YunkanAuthError,
    YunkanConnectionError,
    YunkanSetupRequiredError,
)
from .const import DOMAIN
from .sse import YunkanSSEClient

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)


def signal_event(entry_id: str) -> str:
    """Dispatcher signal fired for every real-time detection event."""
    return f"{DOMAIN}_{entry_id}_event"


@dataclass
class YunkanData:
    """Snapshot of the polled backend state."""

    cameras: dict[str, dict[str, Any]] = field(default_factory=dict)
    license: dict[str, Any] = field(default_factory=dict)
    version: dict[str, Any] = field(default_factory=dict)

    @property
    def is_pro(self) -> bool:
        """Return whether the server is on the Pro tier."""
        return bool(self.license.get("is_pro"))


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
        self.sse = YunkanSSEClient(client, self._handle_event)

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

        data = YunkanData(cameras={cam["id"]: cam for cam in cameras if cam.get("id")})

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

        return data

    async def async_start_stream(self) -> None:
        """Start the SSE event stream."""
        self.sse.start()

    async def async_stop_stream(self) -> None:
        """Stop the SSE event stream."""
        await self.sse.stop()

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
