"""Media source for the Yunkan integration.

Browse per-camera recordings by day and recent events (with thumbnails) from the
HA media library. Everything is served through the HA-authenticated proxy views
and signed with ``async_sign_path`` so external players can fetch a clip without
ever seeing a Yunkan token.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from urllib.parse import quote

from yarl import URL

from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.media_player import BrowseError, MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN, EVENT_CATEGORY_MAP
from .views import URL_EVENT_SNAPSHOT, URL_RECORDING

_LOGGER = logging.getLogger(__name__)

_SIGN_TTL = timedelta(hours=12)
_EVENT_LIMIT = 60


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    """Set up the Yunkan media source."""
    return YunkanMediaSource(hass)


class YunkanMediaSource(MediaSource):
    """Expose Yunkan recordings and events to the HA media browser."""

    name = "Yunkan"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the media source."""
        super().__init__(DOMAIN)
        self.hass = hass

    # ---------------------------------------------------------------- resolve

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a playable media item to a signed proxy URL."""
        parts = item.identifier.split("/")
        coordinator = self._coordinator(parts[0]) if parts else None
        if coordinator is None or len(parts) < 3:
            raise Unresolvable("Unknown Yunkan media item")
        entry_id, kind = parts[0], parts[1]

        if kind == "play":  # recording segment
            recording_id = parts[2]
            path = URL_RECORDING.format(entry_id=entry_id, recording_id=recording_id)
            return PlayMedia(async_sign_path(self.hass, path, _SIGN_TTL), "video/mp4")

        if kind == "img":  # event snapshot
            event_id = parts[2]
            try:
                event = await coordinator.client.async_get_event(int(event_id))
            except Exception as err:  # noqa: BLE001
                raise Unresolvable("Event not found") from err
            snapshot_path = _snapshot_path(event.get("snapshot_url"))
            if not snapshot_path:
                raise Unresolvable("Event has no snapshot")
            url = str(
                URL(URL_EVENT_SNAPSHOT.format(entry_id=entry_id)).with_query(
                    {"path": snapshot_path, "event_id": event_id}
                )
            )
            return PlayMedia(async_sign_path(self.hass, url, _SIGN_TTL), "image/jpeg")

        raise Unresolvable("Unsupported Yunkan media item")

    # ----------------------------------------------------------------- browse

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse the Yunkan media tree."""
        if not item.identifier:
            return self._browse_root()

        parts = item.identifier.split("/")
        coordinator = self._coordinator(parts[0])
        if coordinator is None:
            raise BrowseError("Yunkan is not configured")
        entry_id = parts[0]

        if len(parts) == 1:
            return self._browse_cameras(entry_id, coordinator)
        kind = parts[1]
        if kind == "camera" and len(parts) == 3:
            return self._browse_camera(entry_id, coordinator, parts[2])
        if kind == "rec" and len(parts) == 3:
            return await self._browse_recording_dates(entry_id, coordinator, parts[2])
        if kind == "rec" and len(parts) == 4:
            return await self._browse_recordings(entry_id, coordinator, parts[2], parts[3])
        if kind == "evt" and len(parts) == 3:
            return await self._browse_events(entry_id, coordinator, parts[2])
        raise BrowseError("Unknown Yunkan media path")

    # ------------------------------------------------------------- browse impl

    def _browse_root(self) -> BrowseMediaSource:
        """List configured Yunkan servers (usually one)."""
        children = []
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            coordinator = getattr(entry, "runtime_data", None)
            if coordinator is None:
                continue
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=entry.entry_id,
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=entry.title or "Yunkan",
                    can_play=False,
                    can_expand=True,
                    children_media_class=MediaClass.DIRECTORY,
                )
            )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title="Yunkan",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    def _browse_cameras(self, entry_id: str, coordinator) -> BrowseMediaSource:
        """List cameras for a server."""
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{entry_id}/camera/{quote(cam_id, safe='')}",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=cam.get("name") or cam_id,
                can_play=False,
                can_expand=True,
                children_media_class=MediaClass.DIRECTORY,
            )
            for cam_id, cam in coordinator.data.cameras.items()
        ]
        return _directory(entry_id, "Yunkan", children)

    def _browse_camera(self, entry_id: str, coordinator, camera_id: str) -> BrowseMediaSource:
        """List the recordings and events folders for a camera."""
        cam = coordinator.data.cameras.get(camera_id, {})
        name = cam.get("name") or camera_id
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{entry_id}/rec/{quote(camera_id, safe='')}",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title="Recordings",
                can_play=False,
                can_expand=True,
                children_media_class=MediaClass.DIRECTORY,
            ),
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{entry_id}/evt/{quote(camera_id, safe='')}",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title="Events",
                can_play=False,
                can_expand=True,
                children_media_class=MediaClass.VIDEO,
            ),
        ]
        return _directory(f"{entry_id}/camera/{quote(camera_id, safe='')}", name, children)

    async def _browse_recording_dates(
        self, entry_id: str, coordinator, camera_id: str
    ) -> BrowseMediaSource:
        """List days that have recordings for a camera."""
        try:
            dates = await coordinator.client.async_recording_dates(camera_id)
        except Exception as err:  # noqa: BLE001
            raise BrowseError(f"Could not list recordings: {err}") from err
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{entry_id}/rec/{quote(camera_id, safe='')}/{date}",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=date,
                can_play=False,
                can_expand=True,
                children_media_class=MediaClass.VIDEO,
            )
            for date in dates
        ]
        return _directory(f"{entry_id}/rec/{quote(camera_id, safe='')}", "Recordings", children)

    async def _browse_recordings(
        self, entry_id: str, coordinator, camera_id: str, date: str
    ) -> BrowseMediaSource:
        """List recording segments for a camera on a given day."""
        try:
            timeline = await coordinator.client.async_timeline(camera_id, date)
        except Exception as err:  # noqa: BLE001
            raise BrowseError(f"Could not load timeline: {err}") from err
        children = []
        for rec in timeline.get("recordings", []):
            if rec.get("upload_status") in ("deleted",) or not rec.get("id"):
                continue
            start = str(rec.get("start_time", ""))[11:19] or str(rec.get("id"))
            end = str(rec.get("end_time", ""))[11:19]
            title = f"{start} – {end}" if end else start
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/play/{rec['id']}",
                    media_class=MediaClass.VIDEO,
                    media_content_type="video/mp4",
                    title=title,
                    can_play=True,
                    can_expand=False,
                )
            )
        return _directory(
            f"{entry_id}/rec/{quote(camera_id, safe='')}/{date}", date, children
        )

    async def _browse_events(
        self, entry_id: str, coordinator, camera_id: str
    ) -> BrowseMediaSource:
        """List recent events for a camera, each with a thumbnail."""
        try:
            payload = await coordinator.client.async_list_events(
                camera=camera_id, limit=_EVENT_LIMIT
            )
        except Exception as err:  # noqa: BLE001
            raise BrowseError(f"Could not list events: {err}") from err
        children = []
        for event in payload.get("events", []):
            children.append(self._event_child(entry_id, event))
        return _directory(f"{entry_id}/evt/{quote(camera_id, safe='')}", "Events", children)

    def _event_child(self, entry_id: str, event: dict) -> BrowseMediaSource:
        """Build a browse node for one event."""
        event_type = event.get("event_type", "")
        category = EVENT_CATEGORY_MAP.get(event_type, event_type)
        when = str(event.get("event_time", ""))[:19]
        title = f"{when} · {category}".strip(" ·")
        thumbnail = self._event_thumbnail(entry_id, event)
        recording_id = event.get("recording_id")
        if recording_id:
            return BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{entry_id}/play/{recording_id}",
                media_class=MediaClass.VIDEO,
                media_content_type="video/mp4",
                title=title,
                can_play=True,
                can_expand=False,
                thumbnail=thumbnail,
            )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/img/{event.get('id')}",
            media_class=MediaClass.IMAGE,
            media_content_type="image/jpeg",
            title=title,
            can_play=True,
            can_expand=False,
            thumbnail=thumbnail,
        )

    def _event_thumbnail(self, entry_id: str, event: dict) -> str | None:
        """Build a signed thumbnail URL for an event snapshot."""
        snapshot_path = _snapshot_path(event.get("snapshot_url"))
        if not snapshot_path:
            return None
        url = str(
            URL(URL_EVENT_SNAPSHOT.format(entry_id=entry_id)).with_query(
                {"path": snapshot_path, "w": 320}
            )
        )
        return async_sign_path(self.hass, url, _SIGN_TTL)

    # ----------------------------------------------------------------- helpers

    def _coordinator(self, entry_id: str):
        """Return the coordinator for an entry id, or None."""
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return None
        return getattr(entry, "runtime_data", None)


def _directory(identifier: str, title: str, children: list) -> BrowseMediaSource:
    """Build a directory browse node with children."""
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=identifier,
        media_class=MediaClass.DIRECTORY,
        media_content_type=MediaType.VIDEO,
        title=title,
        can_play=False,
        can_expand=True,
        children=children,
        children_media_class=MediaClass.DIRECTORY,
    )


def _snapshot_path(snapshot_url: str | None) -> str | None:
    """Extract the ``path`` query value from an event ``snapshot_url``."""
    if not snapshot_url:
        return None
    return URL(snapshot_url).query.get("path")
