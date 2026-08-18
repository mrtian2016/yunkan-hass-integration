"""Tests for the sidebar iframe panel (panel.py).

The panel is best-effort sugar on top of ``frontend.async_register_built_in_panel``;
what these tests pin down is our own logic around it: URL resolution from the
options, the stable-but-collision-safe sidebar path, idempotent re-registration
on entry reload, and that unload removes exactly the panel this entry owns.
"""

from __future__ import annotations

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.yunkan.api import (
    YunkanApiError,
    YunkanConnectionError,
    YunkanForbiddenError,
)
from custom_components.yunkan.const import (
    CONF_BASE_URL,
    DOMAIN,
    OPT_PANEL_TITLE,
    OPT_PANEL_URL,
    OPT_SIDEBAR_PANEL,
    SETTING_ALLOW_IFRAME_EMBED,
)
from custom_components.yunkan.panel import (
    async_ensure_server_embed,
    async_remove_panel,
    async_setup_panel,
    panel_url,
)


def _make_entry(hass: HomeAssistant, options: dict) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_BASE_URL: "http://192.168.1.10:23406"},
        options=options,
        title="Yunkan",
    )
    entry.add_to_hass(hass)
    return entry


def _registered_panels(hass: HomeAssistant) -> dict:
    # The frontend integration keeps its panel registry in hass.data.
    return hass.data.get("frontend_panels", {})


def test_panel_url_falls_back_to_connection_url(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, {})
    assert panel_url(entry) == "http://192.168.1.10:23406"


def test_panel_url_override_normalized(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, {OPT_PANEL_URL: "cam.example.com/"})
    assert panel_url(entry) == "http://cam.example.com"
    entry2 = _make_entry(hass, {OPT_PANEL_URL: " https://cam.example.com/ "})
    assert panel_url(entry2) == "https://cam.example.com"


async def test_disabled_option_registers_nothing(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, {})
    async_setup_panel(hass, entry)
    assert "yunkan" not in _registered_panels(hass)
    # Removing without a prior registration must be a no-op, not an error.
    async_remove_panel(hass, entry)


async def test_register_and_remove_roundtrip(hass: HomeAssistant) -> None:
    entry = _make_entry(
        hass,
        {
            OPT_SIDEBAR_PANEL: True,
            OPT_PANEL_TITLE: "云瞰",
            OPT_PANEL_URL: "https://cam.example.com",
        },
    )
    async_setup_panel(hass, entry)

    panels = _registered_panels(hass)
    assert "yunkan" in panels
    panel = panels["yunkan"]
    assert panel.component_name == "iframe"
    assert panel.sidebar_title == "云瞰"
    assert panel.config == {"url": "https://cam.example.com"}

    # Entry reload runs setup again while the old panel is still registered —
    # must not raise (update=True) and must keep the same sidebar path.
    async_setup_panel(hass, entry)
    assert "yunkan" in _registered_panels(hass)

    async_remove_panel(hass, entry)
    assert "yunkan" not in _registered_panels(hass)
    # A second remove (unload after a failed setup, etc.) stays silent.
    async_remove_panel(hass, entry)


async def test_foreign_panel_not_hijacked(hass: HomeAssistant) -> None:
    """A pre-existing 'yunkan' panel from another source must survive us."""
    frontend.async_register_built_in_panel(
        hass,
        "iframe",
        sidebar_title="Someone else",
        frontend_url_path="yunkan",
        config={"url": "http://other.example"},
    )
    entry = _make_entry(hass, {OPT_SIDEBAR_PANEL: True})
    async_setup_panel(hass, entry)

    panels = _registered_panels(hass)
    our_path = f"yunkan-{entry.entry_id[-6:].lower()}"
    # The foreign panel is untouched; we took a suffixed path instead.
    assert panels["yunkan"].sidebar_title == "Someone else"
    assert our_path in panels

    # Removing ours must leave the foreign panel alone.
    async_remove_panel(hass, entry)
    panels = _registered_panels(hass)
    assert "yunkan" in panels
    assert our_path not in panels


async def test_register_failure_is_swallowed(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frontend hiccup must never break entry setup (best-effort contract)."""

    def _boom(hass, component_name, **kwargs):  # noqa: ANN001 - mirrors the real signature
        raise ValueError("frontend unhappy")

    monkeypatch.setattr(frontend, "async_register_built_in_panel", _boom)
    entry = _make_entry(hass, {OPT_SIDEBAR_PANEL: True})
    async_setup_panel(hass, entry)  # must not raise
    # Nothing was recorded, so unload has nothing to remove either.
    async_remove_panel(hass, entry)


# --- server-side embed switch (async_ensure_server_embed) -------------------


class FakeClient:
    """Signature-faithful stand-in for YunkanApiClient's two settings calls."""

    def __init__(
        self,
        values: dict,
        applied: list | None = None,
        get_exc: Exception | None = None,
        update_exc: Exception | None = None,
    ) -> None:
        self.values = values
        self.applied = [SETTING_ALLOW_IFRAME_EMBED] if applied is None else applied
        self.updates: list[dict] = []
        self._get_exc = get_exc
        self._update_exc = update_exc

    async def async_get_settings(self) -> dict:
        if self._get_exc:
            raise self._get_exc
        return dict(self.values)

    async def async_update_settings(self, updates: dict) -> dict:
        if self._update_exc:
            raise self._update_exc
        self.updates.append(updates)
        return {"applied": self.applied, "rejected": [], "restart_required": False}


def _embed_issues(hass: HomeAssistant, entry) -> list:
    reg = ir.async_get(hass)
    ids = (f"embed_not_admin_{entry.entry_id}", f"embed_server_unsupported_{entry.entry_id}")
    return [i for i in ids if reg.async_get_issue(DOMAIN, i)]


async def test_ensure_embed_noop_when_panel_disabled(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, {})
    client = FakeClient({SETTING_ALLOW_IFRAME_EMBED: False})
    assert await async_ensure_server_embed(hass, entry, client) is True
    assert client.updates == []
    assert _embed_issues(hass, entry) == []


async def test_ensure_embed_skips_write_when_already_on(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, {OPT_SIDEBAR_PANEL: True})
    client = FakeClient({SETTING_ALLOW_IFRAME_EMBED: True})
    assert await async_ensure_server_embed(hass, entry, client) is True
    assert client.updates == []
    assert _embed_issues(hass, entry) == []


async def test_ensure_embed_flips_switch_and_clears_issues(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, {OPT_SIDEBAR_PANEL: True})
    # Start from a failure state to prove success clears the stale issue.
    client_bad = FakeClient({}, get_exc=YunkanForbiddenError("admin only"))
    await async_ensure_server_embed(hass, entry, client_bad)
    assert _embed_issues(hass, entry)

    client = FakeClient({SETTING_ALLOW_IFRAME_EMBED: False})
    assert await async_ensure_server_embed(hass, entry, client) is True
    assert client.updates == [{SETTING_ALLOW_IFRAME_EMBED: True}]
    assert _embed_issues(hass, entry) == []


async def test_ensure_embed_old_server_raises_issue(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, {OPT_SIDEBAR_PANEL: True})
    client = FakeClient({"server.public_base_url": ""})  # setting absent = old server
    assert await async_ensure_server_embed(hass, entry, client) is True
    assert client.updates == []
    assert _embed_issues(hass, entry) == [f"embed_server_unsupported_{entry.entry_id}"]
    # Disabling the panel clears the issue again.
    hass.config_entries.async_update_entry(entry, options={OPT_SIDEBAR_PANEL: False})
    assert await async_ensure_server_embed(hass, entry, client) is True
    assert _embed_issues(hass, entry) == []


async def test_ensure_embed_rejected_write_raises_issue(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, {OPT_SIDEBAR_PANEL: True})
    client = FakeClient({SETTING_ALLOW_IFRAME_EMBED: False}, applied=[])
    assert await async_ensure_server_embed(hass, entry, client) is True
    assert _embed_issues(hass, entry) == [f"embed_server_unsupported_{entry.entry_id}"]


async def test_ensure_embed_non_admin_raises_issue(hass: HomeAssistant) -> None:
    """Only a true 403 may be diagnosed as a permission problem."""
    entry = _make_entry(hass, {OPT_SIDEBAR_PANEL: True})
    client = FakeClient({}, get_exc=YunkanForbiddenError("admin only"))
    assert await async_ensure_server_embed(hass, entry, client) is True
    assert _embed_issues(hass, entry) == [f"embed_not_admin_{entry.entry_id}"]


async def test_ensure_embed_forbidden_on_write_raises_issue(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, {OPT_SIDEBAR_PANEL: True})
    client = FakeClient(
        {SETTING_ALLOW_IFRAME_EMBED: False}, update_exc=YunkanForbiddenError("admin only")
    )
    assert await async_ensure_server_embed(hass, entry, client) is True
    assert _embed_issues(hass, entry) == [f"embed_not_admin_{entry.entry_id}"]


async def test_ensure_embed_issues_are_mutually_exclusive(hass: HomeAssistant) -> None:
    """A new diagnosis replaces the other one instead of stacking with it."""
    entry = _make_entry(hass, {OPT_SIDEBAR_PANEL: True})
    await async_ensure_server_embed(
        hass, entry, FakeClient({}, get_exc=YunkanForbiddenError("admin only"))
    )
    assert _embed_issues(hass, entry) == [f"embed_not_admin_{entry.entry_id}"]
    # Account fixed, but the server turns out to predate the setting.
    await async_ensure_server_embed(hass, entry, FakeClient({}))
    assert _embed_issues(hass, entry) == [f"embed_server_unsupported_{entry.entry_id}"]


async def test_ensure_embed_transient_errors_ask_for_retry(hass: HomeAssistant) -> None:
    """Unreachable / 5xx-style failures retry instead of blaming the account."""
    entry = _make_entry(hass, {OPT_SIDEBAR_PANEL: True})
    for exc in (YunkanConnectionError("boom"), YunkanApiError("GET -> HTTP 502: bad gateway")):
        client = FakeClient({}, get_exc=exc)
        assert await async_ensure_server_embed(hass, entry, client) is False
        assert _embed_issues(hass, entry) == []
    # Same for a failure in the write step.
    client = FakeClient(
        {SETTING_ALLOW_IFRAME_EMBED: False}, update_exc=YunkanApiError("PUT -> HTTP 500")
    )
    assert await async_ensure_server_embed(hass, entry, client) is False
    assert _embed_issues(hass, entry) == []
