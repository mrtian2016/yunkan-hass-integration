"""Tests for the REST client using a mocked aiohttp session."""

from __future__ import annotations

import aiohttp
from aioresponses import CallbackResult, aioresponses
import pytest

from custom_components.yunkan.api import (
    YunkanApiClient,
    YunkanAuthError,
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


@pytest.mark.asyncio
async def test_get_settings_returns_values_map() -> None:
    """get_settings unwraps the ``values`` dot-path map from /settings/all."""
    values = {
        "detection.object.enabled": True,
        "detection.face.enabled": False,
    }
    with aioresponses() as mock:
        mock.post(f"{BASE}/api/auth/login", payload=_ok({"access_token": "TOK"}))
        mock.get(
            f"{BASE}/api/settings/all",
            payload=_ok({"values": values, "overrides": [], "defaults": {}}),
        )
        async with aiohttp.ClientSession() as session:
            client = await _client(session)
            assert await client.async_get_settings() == values


@pytest.mark.asyncio
async def test_get_settings_without_values_is_empty() -> None:
    """A malformed settings response yields an empty map, not an error."""
    with aioresponses() as mock:
        mock.post(f"{BASE}/api/auth/login", payload=_ok({"access_token": "TOK"}))
        mock.get(f"{BASE}/api/settings/all", payload=_ok({"overrides": []}))
        async with aiohttp.ClientSession() as session:
            client = await _client(session)
            assert await client.async_get_settings() == {}


@pytest.mark.asyncio
async def test_update_settings_puts_bulk() -> None:
    """update_settings PUTs the dot-path map to the bulk endpoint."""
    with aioresponses() as mock:
        mock.post(f"{BASE}/api/auth/login", payload=_ok({"access_token": "TOK"}))
        mock.put(
            f"{BASE}/api/settings/bulk",
            payload=_ok({"applied": ["detection.face.enabled"], "rejected": []}),
        )
        async with aiohttp.ClientSession() as session:
            client = await _client(session)
            result = await client.async_update_settings(
                {"detection.face.enabled": True}
            )
            assert result["applied"] == ["detection.face.enabled"]


# --------------------------------------------------------------- API tokens


async def _token_client(session: aiohttp.ClientSession) -> YunkanApiClient:
    return YunkanApiClient(session, BASE, api_token="skv_pat_TESTTOKEN")


@pytest.mark.asyncio
async def test_api_token_is_sent_without_logging_in() -> None:
    """A token client authenticates every request and never calls login."""
    seen: dict[str, str] = {}

    def _capture(url, **kwargs):
        seen.update(kwargs.get("headers") or {})
        return CallbackResult(payload=_ok([{"id": "cam01"}]))

    with aioresponses() as mock:
        mock.get(f"{BASE}/api/cameras", callback=_capture)
        async with aiohttp.ClientSession() as session:
            client = await _token_client(session)
            assert client.uses_api_token is True
            assert await client.async_list_cameras() == [{"id": "cam01"}]
    assert seen["Authorization"] == "Bearer skv_pat_TESTTOKEN"


@pytest.mark.asyncio
async def test_api_token_401_raises_instead_of_relogging_in() -> None:
    """A revoked token is an auth error, not something to recover from.

    No login route is mocked on purpose: a re-login attempt would surface as a
    connection error rather than the auth error the reauth flow needs.
    """
    with aioresponses() as mock:
        mock.get(
            f"{BASE}/api/cameras",
            status=401,
            payload={"code": 401, "data": None, "message": "invalid token"},
        )
        async with aiohttp.ClientSession() as session:
            client = await _token_client(session)
            with pytest.raises(YunkanAuthError):
                await client.async_list_cameras()
            assert client.token == "skv_pat_TESTTOKEN"


@pytest.mark.asyncio
async def test_authenticate_with_token_asks_who_it_belongs_to() -> None:
    """Token mode validates the credential against /auth/me, not /auth/login."""
    with aioresponses() as mock:
        mock.get(f"{BASE}/api/auth/me", payload=_ok({"id": 2, "username": "ha"}))
        async with aiohttp.ClientSession() as session:
            client = await _token_client(session)
            assert (await client.async_authenticate())["username"] == "ha"


@pytest.mark.asyncio
async def test_authenticate_with_password_logs_in() -> None:
    """Password mode keeps logging in as before."""
    with aioresponses() as mock:
        mock.post(f"{BASE}/api/auth/login", payload=_ok({"access_token": "TOK"}))
        async with aiohttp.ClientSession() as session:
            client = await _client(session)
            await client.async_authenticate()
            assert client.token == "TOK"


# ------------------------------------------------------------------ handoff


@pytest.mark.asyncio
async def test_handoff_supported() -> None:
    """A server advertising the handoff answers with supported=true."""
    with aioresponses() as mock:
        mock.get(f"{BASE}/api/auth/handoff/config", payload=_ok({"supported": True}))
        async with aiohttp.ClientSession() as session:
            client = YunkanApiClient(session, BASE)
            assert await client.async_handoff_supported() is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (404, {"code": 404, "data": None, "message": "Not Found"}),
        (401, {"message": "Not signed in or session expired"}),
        (200, {"code": 0, "data": {"supported": False}, "message": "success"}),
    ],
)
async def test_handoff_not_supported(status: int, payload: dict) -> None:
    """Anything but an affirmative answer means "ask for a password"."""
    with aioresponses() as mock:
        mock.get(f"{BASE}/api/auth/handoff/config", status=status, payload=payload)
        async with aiohttp.ClientSession() as session:
            client = YunkanApiClient(session, BASE)
            assert await client.async_handoff_supported() is False


@pytest.mark.asyncio
async def test_handoff_exchange_returns_token_and_user() -> None:
    """A valid code is traded for the token and the account it belongs to."""
    with aioresponses() as mock:
        mock.post(
            f"{BASE}/api/auth/handoff/exchange",
            payload=_ok(
                {
                    "token": "skv_pat_NEW",
                    "token_type": "Bearer",
                    "user": {"id": 1, "username": "admin", "role": "admin"},
                }
            ),
        )
        async with aiohttp.ClientSession() as session:
            client = YunkanApiClient(session, BASE)
            result = await client.async_handoff_exchange("CODE", "STATE")
            assert result["token"] == "skv_pat_NEW"
            assert result["user"]["username"] == "admin"


@pytest.mark.asyncio
async def test_handoff_exchange_rejects_a_stale_code() -> None:
    """An expired / reused / mismatched code is a 400 auth error."""
    with aioresponses() as mock:
        mock.post(
            f"{BASE}/api/auth/handoff/exchange",
            status=400,
            payload={"code": 400, "data": None, "message": "HANDOFF_CODE_INVALID"},
        )
        async with aiohttp.ClientSession() as session:
            client = YunkanApiClient(session, BASE)
            with pytest.raises(YunkanAuthError):
                await client.async_handoff_exchange("CODE", "STATE")
