"""Tests for the SSE frame decoding and the reconnect loop's one dead end."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock, patch

import pytest

from custom_components.yunkan.api import YunkanApiError, YunkanAuthError
from custom_components.yunkan.sse import YunkanSSEClient


def _make_client(sink: list[dict]) -> YunkanSSEClient:
    async def _on_event(event: dict) -> None:
        sink.append(event)

    # hass, the REST client and the entry are unused by _dispatch (only the
    # connect loop touches them), so None is fine here.
    return YunkanSSEClient(None, None, None, _on_event)  # type: ignore[arg-type]


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


@pytest.mark.asyncio
async def test_a_refused_credential_stops_the_loop_and_asks_for_reauth() -> None:
    """A revoked token is not something waiting longer can fix.

    Retrying it would go on forever — the entry looking healthy while the log
    fills up — so the loop leaves and hands the problem to the re-auth flow.
    The timeout is the assertion: a loop that backs off instead never returns.
    """

    async def _on_event(event: dict) -> None:
        pass

    entry = Mock()
    sse = YunkanSSEClient(Mock(), Mock(), entry, _on_event)

    with patch.object(
        YunkanSSEClient, "_connect_once", side_effect=YunkanAuthError("revoked")
    ):
        await asyncio.wait_for(sse._run(), timeout=1)

    entry.async_start_reauth.assert_called_once()
    assert not sse.connected


@pytest.mark.asyncio
async def test_an_ordinary_failure_is_still_retried() -> None:
    """Everything that is not the credential goes back through the backoff."""

    async def _on_event(event: dict) -> None:
        pass

    entry = Mock()
    sse = YunkanSSEClient(Mock(), Mock(), entry, _on_event)
    attempts = 0

    async def _fail_then_stop() -> None:
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            sse._closing = True
        raise YunkanApiError("server bounced")

    with (
        patch.object(YunkanSSEClient, "_connect_once", side_effect=_fail_then_stop),
        patch("custom_components.yunkan.sse.asyncio.sleep"),
    ):
        await asyncio.wait_for(sse._run(), timeout=1)

    assert attempts == 2
    entry.async_start_reauth.assert_not_called()
