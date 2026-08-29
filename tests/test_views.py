"""Tests for the HA-authenticated proxy views.

The event snapshot view is reachable by any authenticated Home Assistant user
and proxies upstream with the integration's own backend token, so it must
refuse snapshots belonging to cameras this entry does not expose -- exactly
like the camera snapshot and recording views next to it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.yunkan.views import YunkanEventSnapshotView

VISIBLE_PATH = "events/2026-08-30/cam_1/1.jpg"
HIDDEN_PATH = "events/2026-08-30/cam_hidden/999.jpg"


def _request(**query: str) -> SimpleNamespace:
    """Build a minimal aiohttp-style request carrying the given query."""
    return SimpleNamespace(app={"hass": MagicMock()}, query=query)


def _coordinator(event: dict | None) -> MagicMock:
    """A coordinator exposing one camera, whose client returns ``event``."""
    coordinator = MagicMock()
    coordinator.data.cameras = {"cam_1": {"id": "cam_1"}}
    coordinator.client.async_get_event = AsyncMock(return_value=event)
    coordinator.client.async_event_snapshot = AsyncMock(return_value=b"jpeg-bytes")
    return coordinator


async def _get(coordinator: MagicMock, **query: str):
    """Run the view against a patched coordinator lookup."""
    with patch(
        "custom_components.yunkan.views._get_coordinator", return_value=coordinator
    ):
        return await YunkanEventSnapshotView().get(_request(**query), "entry_1")


def _event(camera_id: str, path: str, event_id: int = 1) -> dict:
    """Shape an event the way the backend returns it."""
    return {
        "id": event_id,
        "camera_id": camera_id,
        "snapshot_url": f"/api/events/snapshot?path={path}",
    }


@pytest.mark.asyncio
async def test_serves_snapshot_of_an_exposed_camera() -> None:
    """The happy path still works and asks for no rendering by default."""
    coordinator = _coordinator(_event("cam_1", VISIBLE_PATH))
    response = await _get(coordinator, event_id="1", path=VISIBLE_PATH)
    assert response.status == 200
    assert response.body == b"jpeg-bytes"
    kwargs = coordinator.client.async_event_snapshot.await_args.kwargs
    assert kwargs["annotate_event_id"] is None
    assert kwargs["crop_event_id"] is None


@pytest.mark.asyncio
async def test_refuses_event_of_a_hidden_camera() -> None:
    """An event id that resolves to a camera we do not expose is a 404.

    This is the hole the check closes: the path alone is well formed, so
    before scoping, guessing it was enough to borrow the backend token.
    """
    coordinator = _coordinator(_event("cam_hidden", HIDDEN_PATH, event_id=999))
    response = await _get(coordinator, event_id="999", path=HIDDEN_PATH)
    assert response.status == 404
    coordinator.client.async_event_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_refuses_exposed_event_id_paired_with_another_path() -> None:
    """The path must be that same event's own snapshot, not just any path."""
    coordinator = _coordinator(_event("cam_1", VISIBLE_PATH))
    response = await _get(coordinator, event_id="1", path=HIDDEN_PATH)
    assert response.status == 404
    coordinator.client.async_event_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_refuses_event_without_a_camera_id() -> None:
    """Ownership that cannot be proven is refused, not served."""
    coordinator = _coordinator(
        {"id": 1, "snapshot_url": f"/api/events/snapshot?path={VISIBLE_PATH}"}
    )
    response = await _get(coordinator, event_id="1", path=VISIBLE_PATH)
    assert response.status == 404


@pytest.mark.asyncio
async def test_refuses_when_the_event_cannot_be_resolved() -> None:
    """A backend error while resolving the event is a 404, never a proxy."""
    coordinator = _coordinator(None)
    coordinator.client.async_get_event = AsyncMock(side_effect=RuntimeError("boom"))
    response = await _get(coordinator, event_id="1", path=VISIBLE_PATH)
    assert response.status == 404
    coordinator.client.async_event_snapshot.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        {"path": VISIBLE_PATH},  # no event id: nothing to scope on
        {"event_id": "abc", "path": VISIBLE_PATH},
        {"event_id": "1"},  # no path
        {"event_id": "1", "path": "recordings/2026-08-30/cam_1/1.mp4"},
        {"event_id": "1", "path": "events/../../etc/passwd"},
    ],
)
async def test_rejects_malformed_requests(query: dict[str, str]) -> None:
    """Malformed input is rejected before any backend call."""
    coordinator = _coordinator(_event("cam_1", VISIBLE_PATH))
    response = await _get(coordinator, **query)
    assert response.status == 400
    coordinator.client.async_get_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_rendering_flags_are_forwarded() -> None:
    """annotate=1 / crop=1 reach the backend as the event id to render with."""
    coordinator = _coordinator(_event("cam_1", VISIBLE_PATH))
    response = await _get(
        coordinator, event_id="1", path=VISIBLE_PATH, annotate="1", crop="1", w="320"
    )
    assert response.status == 200
    kwargs = coordinator.client.async_event_snapshot.await_args.kwargs
    assert kwargs["annotate_event_id"] == 1
    assert kwargs["crop_event_id"] == 1
    assert kwargs["width"] == 320


@pytest.mark.asyncio
async def test_media_source_thumbnails_pass_the_views_own_checks() -> None:
    """The browser's thumbnail URLs must satisfy the scoping the view enforces.

    The two halves have to agree: the view now demands an event id, so a
    thumbnail built without one would 400 on every media browser page.
    """
    from unittest.mock import patch as _patch

    from yarl import URL

    from custom_components.yunkan.media_source import YunkanMediaSource

    event = _event("cam_1", VISIBLE_PATH)
    with _patch(
        "custom_components.yunkan.media_source.async_sign_path",
        side_effect=lambda _hass, url, _ttl: url,
    ):
        url = YunkanMediaSource(MagicMock())._event_thumbnail("entry_1", event)
    assert url is not None
    query = URL(url).query
    assert query["event_id"] == "1"
    assert query["path"] == VISIBLE_PATH
    # Thumbnails stay unboxed; only a full-size event still asks to annotate.
    assert "annotate" not in query

    coordinator = _coordinator(event)
    response = await _get(coordinator, **dict(query))
    assert response.status == 200
