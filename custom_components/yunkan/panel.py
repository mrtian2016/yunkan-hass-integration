"""Sidebar iframe panel embedding the Yunkan web console.

A plain Home Assistant iframe panel (the same mechanism as the built-in
"Website" integration) pointing at the Yunkan origin — *not* Supervisor
Ingress. That means:

- The viewer's browser must be able to reach the panel URL directly (LAN, or
  Yunkan's own public HTTPS access).
- When HA itself is served over HTTPS the panel URL must be HTTPS too, or the
  browser blocks the frame as mixed content.
- Signing in inside a cross-site iframe needs the server-side "Allow embedding
  in Home Assistant" switch so the session cookie is issued with
  ``SameSite=None`` — :func:`async_ensure_server_embed` flips that switch for
  the user (admin account required).

Registration is best-effort: the panel is a convenience shortcut, not a data
path, so a failure here must never break entity setup.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components import frontend
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from . import issues
from .api import (
    YunkanApiClient,
    YunkanApiError,
    YunkanConnectionError,
    YunkanForbiddenError,
    normalize_base_url,
)
from .const import (
    CONF_BASE_URL,
    DEFAULT_PANEL_TITLE,
    DEFAULT_SIDEBAR_PANEL,
    DOMAIN,
    OPT_PANEL_TITLE,
    OPT_PANEL_URL,
    OPT_SIDEBAR_PANEL,
    SETTING_ALLOW_IFRAME_EMBED,
)

_LOGGER = logging.getLogger(__name__)

PANEL_ICON = "mdi:cctv"

#: hass.data bookkeeping: entry_id -> the frontend_url_path this entry
#: registered. Needed so unload removes exactly what we registered — and only
#: that: sidebar paths are a global namespace shared with panel_custom / other
#: integrations, and ``update=True`` would happily overwrite a foreign panel.
DATA_PANELS = f"{DOMAIN}_sidebar_panels"

_PANEL_PATH_BASE = "yunkan"

#: Transient-failure retries for the server embed switch (seconds between
#: attempts). Covers the window where the Yunkan backend restarts right when
#: the entry loads — without a retry the switch would silently stay off until
#: the next HA restart.
_ENSURE_RETRY_DELAYS = (30, 120, 600)


def panel_enabled(entry: ConfigEntry) -> bool:
    """Whether the sidebar panel option is turned on for this entry."""
    return bool(entry.options.get(OPT_SIDEBAR_PANEL, DEFAULT_SIDEBAR_PANEL))


def panel_url(entry: ConfigEntry) -> str:
    """The URL the iframe loads: the override option, or the connection URL."""
    raw = str(entry.options.get(OPT_PANEL_URL, "") or "").strip()
    if not raw:
        return str(entry.data[CONF_BASE_URL])
    return normalize_base_url(raw)


async def async_ensure_server_embed(
    hass: HomeAssistant, entry: ConfigEntry, client: YunkanApiClient
) -> bool:
    """Make sure the server-side embed switch is on when the panel option is.

    The panel only works when the Yunkan server issues its session cookie with
    ``SameSite=None`` ("Allow embedding in Home Assistant", default off).
    Flipping that by hand is buried deep in the web console, so while the panel
    option is enabled the integration keeps the switch on — including
    re-asserting it on entry reloads. This is the documented contract: turning
    the switch off for good means turning the panel option off first (which the
    integration deliberately never mirrors back to the server — another HA
    instance or a manual embed may rely on it).

    Needs an admin account; definitive failures surface as repair issues
    rather than breaking entry setup.

    Returns True on a definitive outcome (switch on / issue raised / panel
    disabled) and False on a transient failure worth retrying — see
    :func:`async_ensure_server_embed_retry`.
    """
    if not panel_enabled(entry):
        issues.async_clear_embed_issues(hass, entry.entry_id)
        return True
    try:
        values = await client.async_get_settings()
        if SETTING_ALLOW_IFRAME_EMBED not in values:
            # Server predates the setting — flipping it is impossible, and the
            # embedded sign-in will loop. Tell the user instead of guessing.
            issues.async_raise_embed_unsupported(hass, entry.entry_id)
            return True
        if not values[SETTING_ALLOW_IFRAME_EMBED]:
            result = await client.async_update_settings(
                {SETTING_ALLOW_IFRAME_EMBED: True}
            )
            applied = result.get("applied") if isinstance(result, dict) else None
            if not (isinstance(applied, list) and SETTING_ALLOW_IFRAME_EMBED in applied):
                # The server knows the key but refused it — treat like an
                # unsupported server rather than pretending the panel works.
                issues.async_raise_embed_unsupported(hass, entry.entry_id)
                return True
            _LOGGER.info(
                "Enabled 'Allow embedding in Home Assistant' on the Yunkan server"
            )
    except YunkanForbiddenError:
        # Non-license 403 on the settings API = the account is not an admin.
        # Only this status carries that meaning — a 500/502/504 must not be
        # diagnosed as a permission problem (the advice would be wrong).
        issues.async_raise_embed_not_admin(hass, entry.entry_id)
        return True
    except YunkanConnectionError:
        _LOGGER.debug("Could not verify the server embed switch (unreachable)")
        return False
    except YunkanApiError as err:
        # Proxy 5xx, auth churn mid-restart, … — transient server trouble, not
        # something the user can act on. Retry instead of raising an issue.
        _LOGGER.debug("Could not verify the server embed switch: %s", err)
        return False
    issues.async_clear_embed_issues(hass, entry.entry_id)
    return True


async def async_ensure_server_embed_retry(
    hass: HomeAssistant, entry: ConfigEntry, client: YunkanApiClient
) -> None:
    """Run the ensure step with retries, off the entry-setup critical path.

    Meant for ``entry.async_create_background_task`` so a slow or briefly
    unreachable server neither delays the event stream nor leaves the switch
    silently unflipped (the retries cover backend self-restarts, which this
    very feature triggers when it applies the setting).
    """
    for delay in (0, *_ENSURE_RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        if await async_ensure_server_embed(hass, entry, client):
            return
    _LOGGER.warning(
        "Could not reach the Yunkan server to enable 'Allow embedding in Home"
        " Assistant'; sign-in inside the sidebar panel may loop until the"
        " integration is reloaded or the switch is enabled in the web console"
    )


def _pick_url_path(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """A stable sidebar URL path that never claims someone else's panel.

    Sidebar paths are global: panel_custom / other integrations may already own
    ``yunkan``, and registering over it with ``update=True`` would silently
    replace — and later delete — their panel. Prefer ``yunkan``, fall back to a
    suffixed path (entry_id *tail* — the ULID head is a timestamp with no
    entropy), and give up (None) rather than hijack when both are taken.
    """
    panels: dict[str, str] = hass.data.setdefault(DATA_PANELS, {})
    existing = panels.get(entry.entry_id)
    if existing:
        return existing
    registered = hass.data.get(getattr(frontend, "DATA_PANELS", "frontend_panels")) or {}
    for candidate in (_PANEL_PATH_BASE, f"{_PANEL_PATH_BASE}-{entry.entry_id[-6:].lower()}"):
        if candidate not in registered:
            return candidate
    return None


@callback
def async_setup_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register the sidebar panel if the option is enabled (best-effort)."""
    if not panel_enabled(entry):
        return
    path = _pick_url_path(hass, entry)
    if path is None:
        _LOGGER.warning(
            "Not registering the Yunkan sidebar panel: another panel already"
            " owns the 'yunkan' sidebar path"
        )
        return
    title = str(entry.options.get(OPT_PANEL_TITLE) or DEFAULT_PANEL_TITLE)
    try:
        frontend.async_register_built_in_panel(
            hass,
            "iframe",
            sidebar_title=title,
            sidebar_icon=PANEL_ICON,
            frontend_url_path=path,
            config={"url": panel_url(entry)},
            require_admin=False,
            # Idempotent re-register of *our own* path: entry reloads run setup
            # again while the old panel may still be registered. Foreign paths
            # are never chosen (see _pick_url_path), so this cannot clobber one.
            update=True,
        )
    except Exception:  # noqa: BLE001 - the panel must never break entry setup
        _LOGGER.warning("Could not register the Yunkan sidebar panel", exc_info=True)
        return
    hass.data[DATA_PANELS][entry.entry_id] = path


@callback
def async_remove_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove this entry's sidebar panel, if it registered one."""
    panels: dict[str, str] = hass.data.get(DATA_PANELS) or {}
    path = panels.pop(entry.entry_id, None)
    if not path:
        return
    try:
        frontend.async_remove_panel(hass, path)
    except Exception:  # noqa: BLE001 - best-effort, same as registration
        _LOGGER.debug("Could not remove the Yunkan sidebar panel", exc_info=True)
