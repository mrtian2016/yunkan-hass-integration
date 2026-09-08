"""Authorization handoff: the callback view and its pending-flow registry.

The integration does not collect the Yunkan account password any more. The
config flow sends the user to the server's authorization page, they approve
there with whatever sign-in method their account uses, and the server redirects
their browser back to the view below carrying a one-time code. The view hands
that code to the waiting config flow, which trades it for a long-lived API
token.

The ``state`` registry is what ties the two halves together: the flow records
an unguessable random string before opening the page, and the redirect brings
it back so the view knows which of the (possibly several) in-flight flows this
browser belongs to.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import InvalidData, UnknownFlow, UnknownStep
from homeassistant.helpers.http import KEY_HASS

from .const import DATA_HANDOFF_FLOWS, DOMAIN, HANDOFF_CALLBACK_PATH

_LOGGER = logging.getLogger(__name__)

_VIEW_REGISTERED = "handoff_view_registered"

# Mirrors what Home Assistant's own OAuth2 callback returns: the page is opened
# in a window the user never interacts with, so it only has to close itself and
# say something sensible if the browser refuses to.
_CLOSE_PAGE = (
    "<script>window.close()</script>"
    "You can close this window now and go back to Home Assistant."
)

_INVALID_STATE_PAGE = (
    "This authorization link is no longer valid. "
    "Start again from Home Assistant."
)


@callback
def async_register_handoff_view(hass: HomeAssistant) -> None:
    """Register the callback view once for the whole integration.

    Called both from ``async_setup`` and from the config flow itself: setting
    up an integration does not run ``async_setup`` before its config flow
    starts, so a first-time install would otherwise redirect to a view that
    does not exist yet.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_VIEW_REGISTERED):
        return
    hass.http.register_view(YunkanHandoffCallbackView())
    domain_data[_VIEW_REGISTERED] = True


@callback
def async_register_handoff_state(hass: HomeAssistant, state: str, flow_id: str) -> None:
    """Remember which config flow a pending authorization belongs to."""
    hass.data.setdefault(DOMAIN, {}).setdefault(DATA_HANDOFF_FLOWS, {})[state] = flow_id


@callback
def async_forget_handoff_state(hass: HomeAssistant, state: str | None) -> None:
    """Drop a pending authorization (finished, failed or abandoned)."""
    if not state:
        return
    hass.data.get(DOMAIN, {}).get(DATA_HANDOFF_FLOWS, {}).pop(state, None)


class YunkanHandoffCallbackView(HomeAssistantView):
    """Receive the browser redirect carrying the authorization result.

    ``requires_auth`` is False on purpose. This request is a plain cross-site
    navigation coming from the Yunkan authorization page, so it carries no Home
    Assistant session and could not be authenticated even in principle. It is
    safe unauthenticated because it is inert on its own: it hands over no data,
    and the only thing it can do is advance one already-running config flow —
    the one the unguessable ``state`` in the URL points at. Without a matching
    state it does nothing at all, and the code it forwards is worthless to
    anyone but that flow (single use, short lived, and the exchange re-checks
    the same state).
    """

    url = HANDOFF_CALLBACK_PATH
    name = "api:yunkan:authorize"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Pass the authorization result to the waiting config flow."""
        hass: HomeAssistant = request.app[KEY_HASS]
        state = request.query.get("state")
        flows = hass.data.get(DOMAIN, {}).get(DATA_HANDOFF_FLOWS, {})
        # ``pop``, not ``get``: the state is single use. Left in the registry it
        # would keep this URL alive until the flow finished, and a plain browser
        # refresh of the "you can close this window" page would re-run
        # ``async_configure`` on a flow that has already moved on -- either
        # creating the entry from under the frontend (which then sees
        # UnknownFlow and tells the user the flow no longer exists) or burning
        # the one-shot code on a failed second exchange.
        flow_id = flows.pop(state, None) if state else None
        if flow_id is None:
            _LOGGER.debug("Authorization callback with unknown state")
            return web.Response(
                text=_INVALID_STATE_PAGE, status=400, content_type="text/html"
            )

        result: dict[str, Any] = {}
        if code := request.query.get("code"):
            result["code"] = code
        else:
            # Cancelled on the authorization page, or refused for some other
            # reason; either way the flow shows the user what happened.
            result["error"] = request.query.get("error") or "access_denied"

        try:
            await hass.config_entries.flow.async_configure(flow_id, result)
        except (UnknownFlow, UnknownStep, InvalidData):
            # UnknownFlow: the user closed the config dialog while the page was
            # open. UnknownStep / InvalidData: the flow is no longer waiting on
            # the external step (a late or duplicated callback).
            return web.Response(
                text=_INVALID_STATE_PAGE, status=400, content_type="text/html"
            )

        return web.Response(text=_CLOSE_PAGE, content_type="text/html")
