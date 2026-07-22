"""Tests for the REST client using a mocked aiohttp session."""

from __future__ import annotations

import aiohttp
from aioresponses import aioresponses
import pytest

from custom_components.yunkan.api import (
    YunkanApiClient,
    YunkanProRequiredError,
    YunkanSetupRequiredError,
)

BASE = "http://host:23406"


def _ok(data):
    return {"code": 0, "data": data, "message": "success"}


async def _client(session: aiohttp.ClientSession) -> YunkanApiClient:
    return YunkanApiClient(session, BASE, "admin", "secret")


@pytest.mark.asyncio
async def test_login_sets_token() -> None:
    """A successful login caches the access token and returns the user."""
    with aioresponses() as mock:
        mock.post(f"{BASE}/api/auth/login", payload=_ok({"access_token": "TOK", "user": {"id": 1}}))
        async with aiohttp.ClientSession() as session:
            client = await _client(session)
            user = await client.async_login()
            assert client.token == "TOK"
            assert user["id"] == 1


@pytest.mark.asyncio
async def test_login_setup_required() -> None:
    """A 412 SETUP_REQUIRED login raises the setup error."""
    with aioresponses() as mock:
        mock.post(
            f"{BASE}/api/auth/login",
            status=412,
            payload={"code": 412, "data": None, "message": "SETUP_REQUIRED"},
        )
        async with aiohttp.ClientSession() as session:
            client = await _client(session)
            with pytest.raises(YunkanSetupRequiredError):
                await client.async_login()


@pytest.mark.asyncio
async def test_list_cameras_unwraps_envelope() -> None:
    """list_cameras logs in on demand and returns the data array."""
    with aioresponses() as mock:
        mock.post(f"{BASE}/api/auth/login", payload=_ok({"access_token": "TOK"}))
        mock.get(f"{BASE}/api/cameras", payload=_ok([{"id": "cam01", "name": "Door"}]))
        async with aiohttp.ClientSession() as session:
            client = await _client(session)
            cameras = await client.async_list_cameras()
            assert cameras == [{"id": "cam01", "name": "Door"}]


@pytest.mark.asyncio
async def test_pro_required_error() -> None:
    """A 403 LICENSE_REQUIRED response raises with the parsed status."""
    with aioresponses() as mock:
        mock.post(f"{BASE}/api/auth/login", payload=_ok({"access_token": "TOK"}))
        mock.post(
            f"{BASE}/api/detection/toggle/cam01",
            status=403,
            payload={"code": 403, "data": None, "message": "LICENSE_REQUIRED:unlicensed"},
        )
        async with aiohttp.ClientSession() as session:
            client = await _client(session)
            with pytest.raises(YunkanProRequiredError) as err:
                await client.async_toggle_detection("cam01")
            assert err.value.license_status == "unlicensed"


@pytest.mark.asyncio
async def test_401_triggers_reauth_and_retry() -> None:
    """A 401 on a request re-logs in and retries once."""
    with aioresponses() as mock:
        # Initial login, then a 401, then a re-login, then success.
        mock.post(f"{BASE}/api/auth/login", payload=_ok({"access_token": "TOK1"}))
        mock.get(
            f"{BASE}/api/cameras",
            status=401,
            payload={"code": 401, "data": None, "message": "expired"},
        )
        mock.post(f"{BASE}/api/auth/login", payload=_ok({"access_token": "TOK2"}))
        mock.get(f"{BASE}/api/cameras", payload=_ok([{"id": "cam01"}]))
        async with aiohttp.ClientSession() as session:
            client = await _client(session)
            cameras = await client.async_list_cameras()
            assert cameras == [{"id": "cam01"}]
            assert client.token == "TOK2"


@pytest.mark.asyncio
async def test_snapshot_204_returns_none() -> None:
    """A 204 from the snapshot endpoint (never captured) yields None."""
    with aioresponses() as mock:
        mock.post(f"{BASE}/api/auth/login", payload=_ok({"access_token": "TOK"}))
        mock.get(f"{BASE}/api/cameras/cam01/snapshot", status=204)
        async with aiohttp.ClientSession() as session:
            client = await _client(session)
            assert await client.async_snapshot("cam01") is None


@pytest.mark.asyncio
async def test_sse_stream_url() -> None:
    """The SSE stream URL carries the ticket as a query parameter."""
    async with aiohttp.ClientSession() as session:
        client = await _client(session)
        assert client.sse_stream_url("TICK") == f"{BASE}/api/events/stream?ticket=TICK"
