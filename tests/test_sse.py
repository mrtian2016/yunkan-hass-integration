"""Tests for the SSE frame decoding."""

from __future__ import annotations

import pytest

from custom_components.yunkan.sse import YunkanSSEClient


def _make_client(sink: list[dict]) -> YunkanSSEClient:
    async def _on_event(event: dict) -> None:
        sink.append(event)

    # hass and the REST client are unused by _dispatch, so None is fine here.
    return YunkanSSEClient(None, None, _on_event)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_dispatch_decodes_event() -> None:
    """A JSON data frame is decoded and forwarded to the callback."""
    sink: list[dict] = []
    client = _make_client(sink)
    await client._dispatch(['{"id": 5, "camera_id": "cam01", "event_type": "person"}'])
    assert sink == [{"id": 5, "camera_id": "cam01", "event_type": "person"}]


@pytest.mark.asyncio
async def test_dispatch_ignores_hello_frame() -> None:
    """The empty {} hello frame is not forwarded."""
    sink: list[dict] = []
    client = _make_client(sink)
    await client._dispatch(["{}"])
    assert sink == []


@pytest.mark.asyncio
async def test_dispatch_ignores_bad_json() -> None:
    """A malformed frame is dropped without raising."""
    sink: list[dict] = []
    client = _make_client(sink)
    await client._dispatch(["not json"])
    assert sink == []


@pytest.mark.asyncio
async def test_dispatch_joins_multiline_data() -> None:
    """Multiple data lines are joined with newlines before decoding."""
    sink: list[dict] = []
    client = _make_client(sink)
    await client._dispatch(['{"a":', '1}'])
    assert sink == [{"a": 1}]
