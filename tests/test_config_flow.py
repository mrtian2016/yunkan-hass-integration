"""Tests for the config flow, with the authorization handoff at its centre.

The handoff spans three parties: the flow opens the server's authorization
page, the user's browser comes back to the callback view, and the flow trades
the code it carried for an API token. The tests below drive that whole loop —
including a real HTTP request to the view — because the interesting failures
(a state that no longer resolves, a code that no longer works, a user who said
no) all live in the seams between those parties.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import Mock, patch

import aiohttp
from aioresponses import aioresponses
import pytest
from yarl import URL

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.http import current_request
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.yunkan.const import (
    CONF_API_TOKEN,
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_USE_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DOMAIN,
)

BASE = "http://cam.local:23406"
HOST = "cam.local"
HA_URL = "http://ha.local:8123"
REDIRECT_URI = f"{HA_URL}/api/yunkan/authorize"


@pytest.fixture(autouse=True)
def _enable_custom_integration(enable_custom_integrations: None) -> None:
    """Let the flow manager find custom_components/yunkan."""


@pytest.fixture(autouse=True)
async def _http(hass: HomeAssistant) -> None:
    """Set up http (the flow registers a view) and stub the heavy platforms.

    camera / stream / media_source are declared dependencies but pull in codecs
    the test environment has no use for; marking them set up keeps the flow's
    integration load from dragging them in.
    """
    for dependency in ("camera", "stream", "media_source"):
        hass.config.components.add(dependency)
    assert await async_setup_component(hass, "http", {})


def _ok(data: Any) -> dict[str, Any]:
    """Shape a successful backend envelope."""
    return {"code": 0, "data": data, "message": "success"}


def _mock_probe(mock: aioresponses, *, handoff: bool) -> None:
    """Answer the two unauthenticated probes the flow makes for a server."""
    mock.get(f"{BASE}/api/auth/setup-status", payload=_ok({"needs_setup": False}))
    if handoff:
        mock.get(f"{BASE}/api/auth/handoff/config", payload=_ok({"supported": True}))
    else:
        # What an older server really answers: the route does not exist, so
        # FastAPI's own 404 body comes back, not the API's envelope. The probe
        # has to read that as "no handoff here" rather than as a broken server.
        mock.get(
            f"{BASE}/api/auth/handoff/config",
            status=404,
            payload={"detail": "Not Found"},
        )


@contextmanager
def _browsing_from(url: str) -> Iterator[None]:
    """Pretend the flow is being driven from a browser on ``url``.

    Home Assistant puts the incoming request in this context variable; the
    redirect-URI fallback reads it to check a configured address is one this
    browser is actually using.
    """
    token = current_request.set(Mock(url=URL(url)))
    try:
        yield
    finally:
        current_request.reset(token)


async def _submit_address(hass: HomeAssistant, *, handoff: bool = True) -> dict[str, Any]:
    """Run the flow through the address step and return where it landed."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    with (
        aioresponses() as mock,
        patch("custom_components.yunkan.config_flow.get_url", return_value=HA_URL),
    ):
        _mock_probe(mock, handoff=handoff)
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: BASE, CONF_VERIFY_SSL: True}
        )


async def _callback(hass_client_no_auth, query: str) -> int:
    """Hit the callback view the way the user's browser would."""
    client = await hass_client_no_auth()
    response = await client.get(f"/api/yunkan/authorize?{query}")
    return response.status


# ------------------------------------------------------------- happy paths


async def test_address_step_sends_the_user_to_the_server(hass: HomeAssistant) -> None:
    """A server that supports the handoff takes over the sign-in."""
    result = await _submit_address(hass)

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert result["step_id"] == "handoff"
    url = URL(result["url"])
    assert str(url.with_query(None)) == f"{BASE}/authorize"
    assert url.query["client"] == "Home Assistant"
    assert url.query["redirect_uri"] == REDIRECT_URI
    assert len(url.query["state"]) >= 32
    # A "+" here would arrive at the server as a plus sign, not a space.
    assert "client=Home%20Assistant" in result["url"]


async def test_authorization_becomes_a_token_entry(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    """Approving on the server creates an entry holding a token, not a password."""
    result = await _submit_address(hass)
    flow_id = result["flow_id"]
    state = URL(result["url"]).query["state"]

    assert await _callback(hass_client_no_auth, f"state={state}&code=THECODE") == 200

    with (
        aioresponses() as mock,
        patch("custom_components.yunkan.async_setup_entry", return_value=True),
    ):
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
        result = await hass.config_entries.flow.async_configure(flow_id)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == HOST
    assert result["data"] == {
        CONF_BASE_URL: BASE,
        CONF_USERNAME: "admin",
        CONF_API_TOKEN: "skv_pat_NEW",
        CONF_VERIFY_SSL: True,
    }
    assert CONF_PASSWORD not in result["data"]


async def test_old_server_still_asks_for_a_password(hass: HomeAssistant) -> None:
    """A server without the handoff falls back to the original credential form."""
    result = await _submit_address(hass, handoff=False)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"

    with (
        aioresponses() as mock,
        patch("custom_components.yunkan.async_setup_entry", return_value=True),
    ):
        mock.post(f"{BASE}/api/auth/login", payload=_ok({"access_token": "TOK"}))
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "admin", CONF_PASSWORD: "secret"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_BASE_URL: BASE,
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "secret",
        CONF_VERIFY_SSL: True,
    }
    assert CONF_API_TOKEN not in result["data"]


async def test_asking_for_the_password_form_skips_the_handoff(
    hass: HomeAssistant,
) -> None:
    """The opt-out goes straight to the password form, without probing.

    The box is there for people whose browser cannot reach the authorization
    page even though the server offers it; asking the server about it first
    would only be one more thing to go wrong on their way out.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with aioresponses() as mock:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_BASE_URL: BASE, CONF_VERIFY_SSL: True, CONF_USE_PASSWORD: True},
        )
        assert mock.requests == {}

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"


# ----------------------------------------------------------- failure paths


async def test_bad_credentials_keep_the_credential_form(hass: HomeAssistant) -> None:
    """The password path reports a rejected login as it always has."""
    result = await _submit_address(hass, handoff=False)

    with aioresponses() as mock:
        mock.post(
            f"{BASE}/api/auth/login",
            status=401,
            payload={"code": 401, "data": None, "message": "bad password"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "admin", CONF_PASSWORD: "wrong"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_an_account_needing_a_second_step_says_so(hass: HomeAssistant) -> None:
    """A password that is right but not enough is not "wrong password".

    Reachable now that the password form can be asked for on a server that is
    new enough to have two-step verification: the server accepts the password
    and answers that a second step is due, which nothing here can supply.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_BASE_URL: BASE, CONF_VERIFY_SSL: True, CONF_USE_PASSWORD: True},
    )
    assert result["step_id"] == "credentials"

    with aioresponses() as mock:
        mock.post(
            f"{BASE}/api/auth/login",
            payload=_ok({"mfa_required": True, "methods": ["totp"]}),
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "admin", CONF_PASSWORD: "secret"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"
    assert result["errors"] == {"base": "mfa_required"}


async def test_expired_code_returns_to_the_address_step(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    """A code the server refuses is reported, not swallowed."""
    result = await _submit_address(hass)
    flow_id = result["flow_id"]
    state = URL(result["url"]).query["state"]

    assert await _callback(hass_client_no_auth, f"state={state}&code=STALE") == 200

    with aioresponses() as mock:
        mock.post(
            f"{BASE}/api/auth/handoff/exchange",
            status=400,
            payload={"code": 400, "data": None, "message": "HANDOFF_CODE_INVALID"},
        )
        result = await hass.config_entries.flow.async_configure(flow_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "handoff_failed"}


async def test_an_exchange_without_a_token_is_a_failure(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    """A 200 carrying no token is a failure, not an entry with no credential."""
    result = await _submit_address(hass)
    flow_id = result["flow_id"]
    state = URL(result["url"]).query["state"]

    assert await _callback(hass_client_no_auth, f"state={state}&code=THECODE") == 200

    with aioresponses() as mock:
        mock.post(
            f"{BASE}/api/auth/handoff/exchange",
            payload=_ok({"user": {"id": 1, "username": "admin"}}),
        )
        result = await hass.config_entries.flow.async_configure(flow_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "handoff_failed"}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_the_callback_link_only_works_once(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    """A replayed redirect is refused and leaves the flow exactly as it was.

    The "you can close this window" page is a normal page: it gets refreshed,
    restored with the browser session, or prefetched. Only the first visit may
    reach the flow — a second one carrying a different code must not be able to
    swap out the one the flow is about to redeem.
    """
    result = await _submit_address(hass)
    flow_id = result["flow_id"]
    state = URL(result["url"]).query["state"]

    assert await _callback(hass_client_no_auth, f"state={state}&code=FIRST") == 200
    assert await _callback(hass_client_no_auth, f"state={state}&code=SECOND") == 400

    with (
        aioresponses() as mock,
        patch("custom_components.yunkan.async_setup_entry", return_value=True),
    ):
        mock.post(
            f"{BASE}/api/auth/handoff/exchange",
            payload=_ok({"token": "skv_pat_NEW", "user": {"username": "admin"}}),
        )
        result = await hass.config_entries.flow.async_configure(flow_id)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    exchanges = mock.requests[("POST", URL(f"{BASE}/api/auth/handoff/exchange"))]
    assert len(exchanges) == 1
    assert exchanges[0].kwargs["json"] == {"code": "FIRST", "state": state}


async def test_cancelling_on_the_server_is_reported(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    """Pressing cancel on the authorization page comes back as its own error."""
    result = await _submit_address(hass)
    flow_id = result["flow_id"]
    state = URL(result["url"]).query["state"]

    assert (
        await _callback(hass_client_no_auth, f"state={state}&error=access_denied") == 200
    )
    result = await hass.config_entries.flow.async_configure(flow_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "access_denied"}


async def test_callback_with_an_unknown_state_is_refused(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    """A redirect that matches no pending flow does nothing at all."""
    await _submit_address(hass)
    assert await _callback(hass_client_no_auth, "state=not-a-real-state&code=X") == 400


async def test_state_is_forgotten_when_the_flow_goes_away(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    """Abandoning the dialog invalidates the link the server would come back to."""
    result = await _submit_address(hass)
    state = URL(result["url"]).query["state"]

    hass.config_entries.flow.async_abort(result["flow_id"])

    assert await _callback(hass_client_no_auth, f"state={state}&code=THECODE") == 400


async def test_unknown_home_assistant_address_is_an_error(hass: HomeAssistant) -> None:
    """Without an address of its own, there is nowhere for the server to return to."""
    hass.config.internal_url = None
    hass.config.external_url = None
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with (
        aioresponses() as mock,
        patch(
            "custom_components.yunkan.config_flow.get_url",
            side_effect=NoURLAvailableError,
        ),
    ):
        _mock_probe(mock, handoff=True)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: BASE, CONF_VERIFY_SSL: True}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_url"}


async def test_a_configured_address_for_another_host_is_not_used(
    hass: HomeAssistant,
) -> None:
    """A configured URL that is not the one this browser is on is no use.

    Redirecting the authorization page at an address the user's browser cannot
    reach would fail far away from here — as a window that never comes back —
    so an address we cannot confirm is reported as no address at all.
    """
    hass.config.internal_url = "http://nas.local:8123"
    hass.config.external_url = None
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with (
        aioresponses() as mock,
        patch(
            "custom_components.yunkan.config_flow.get_url",
            side_effect=NoURLAvailableError,
        ),
        _browsing_from(HA_URL),
    ):
        _mock_probe(mock, handoff=True)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: BASE, CONF_VERIFY_SSL: True}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_url"}


async def test_a_configured_address_for_this_host_is_used(hass: HomeAssistant) -> None:
    """The same fallback does work when it is the address the browser is on."""
    hass.config.internal_url = HA_URL
    hass.config.external_url = None
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with (
        aioresponses() as mock,
        patch(
            "custom_components.yunkan.config_flow.get_url",
            side_effect=NoURLAvailableError,
        ),
        _browsing_from(HA_URL),
    ):
        _mock_probe(mock, handoff=True)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: BASE, CONF_VERIFY_SSL: True}
        )

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert URL(result["url"]).query["redirect_uri"] == REDIRECT_URI


async def test_unreachable_server_reports_the_connection(hass: HomeAssistant) -> None:
    """A server that does not answer is a connection error, not a fallback."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with aioresponses() as mock:
        mock.get(
            f"{BASE}/api/auth/setup-status",
            exception=aiohttp.ClientConnectionError("no route"),
        )
        mock.get(
            f"{BASE}/api/auth/handoff/config",
            exception=aiohttp.ClientConnectionError("no route"),
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: BASE, CONF_VERIFY_SSL: True}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


# ----------------------------------------------------------------- reauth


def _password_entry(hass: HomeAssistant) -> MockConfigEntry:
    """An entry as it looked before the handoff existed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOST,
        data={
            CONF_BASE_URL: BASE,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_SSL: True,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_reauth_upgrades_a_password_entry_to_a_token(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    """Re-authenticating against an updated server drops the stored password."""
    entry = _password_entry(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    flow_id = result["flow_id"]

    with (
        aioresponses() as mock,
        patch("custom_components.yunkan.config_flow.get_url", return_value=HA_URL),
    ):
        _mock_probe(mock, handoff=True)
        result = await hass.config_entries.flow.async_configure(flow_id, {})

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    state = URL(result["url"]).query["state"]
    assert await _callback(hass_client_no_auth, f"state={state}&code=THECODE") == 200

    with (
        aioresponses() as mock,
        patch("custom_components.yunkan.async_setup_entry", return_value=True),
    ):
        mock.post(
            f"{BASE}/api/auth/handoff/exchange",
            payload=_ok({"token": "skv_pat_NEW", "user": {"username": "admin"}}),
        )
        result = await hass.config_entries.flow.async_configure(flow_id)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_TOKEN] == "skv_pat_NEW"
    assert CONF_PASSWORD not in entry.data


async def test_reauth_can_ask_for_the_password_form_outright(
    hass: HomeAssistant,
) -> None:
    """The same opt-out is on the re-authorization step, and skips the probe."""
    entry = _password_entry(hass)
    result = await entry.start_reauth_flow(hass)

    with aioresponses() as mock:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USE_PASSWORD: True}
        )
        assert mock.requests == {}

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_password"


async def test_reauth_against_an_old_server_asks_for_the_password(
    hass: HomeAssistant,
) -> None:
    """Without the handoff, reauth is still a single password field."""
    entry = _password_entry(hass)
    result = await entry.start_reauth_flow(hass)
    flow_id = result["flow_id"]

    with aioresponses() as mock:
        _mock_probe(mock, handoff=False)
        result = await hass.config_entries.flow.async_configure(flow_id, {})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_password"

    with (
        aioresponses() as mock,
        patch("custom_components.yunkan.async_setup_entry", return_value=True),
    ):
        mock.post(f"{BASE}/api/auth/login", payload=_ok({"access_token": "TOK"}))
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_PASSWORD: "new-secret"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-secret"
    assert entry.data[CONF_USERNAME] == "admin"
    assert CONF_API_TOKEN not in entry.data
