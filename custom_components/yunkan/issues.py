"""Repair issues raised by the Yunkan integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_PRO_ISSUE_ID = "pro_required"
_LEARN_MORE_URL = "https://yun-kan.com/pricing"


def async_raise_pro_required(hass: HomeAssistant, license_status: str) -> None:
    """Surface a repair issue explaining that a Pro license is required."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _PRO_ISSUE_ID,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=_PRO_ISSUE_ID,
        translation_placeholders={"status": license_status},
        learn_more_url=_LEARN_MORE_URL,
    )


def async_clear_pro_required(hass: HomeAssistant) -> None:
    """Clear the Pro-required repair issue once the tier is Pro again."""
    ir.async_delete_issue(hass, DOMAIN, _PRO_ISSUE_ID)


# --- sidebar panel: server-side embed switch (see panel.py) -----------------
#
# Issue ids carry the entry_id so they can be cleaned up precisely when the
# entry goes away; the translation_key stays the shared static one. The two
# issues are mutually exclusive diagnoses of the same step, so raising one
# always clears the other — otherwise a stale "not admin" would survive next
# to "server too old" after the user fixes the account, telling them to redo
# what they just did.

_EMBED_NOT_ADMIN = "embed_not_admin"
_EMBED_UNSUPPORTED = "embed_server_unsupported"


def async_raise_embed_not_admin(hass: HomeAssistant, entry_id: str) -> None:
    """The panel is on but the account could not flip the server embed switch."""
    ir.async_delete_issue(hass, DOMAIN, f"{_EMBED_UNSUPPORTED}_{entry_id}")
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{_EMBED_NOT_ADMIN}_{entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=_EMBED_NOT_ADMIN,
    )


def async_raise_embed_unsupported(hass: HomeAssistant, entry_id: str) -> None:
    """The panel is on but the server predates the embed setting."""
    ir.async_delete_issue(hass, DOMAIN, f"{_EMBED_NOT_ADMIN}_{entry_id}")
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{_EMBED_UNSUPPORTED}_{entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=_EMBED_UNSUPPORTED,
    )


def async_clear_embed_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Clear both embed issues (panel disabled, or the switch is now on)."""
    ir.async_delete_issue(hass, DOMAIN, f"{_EMBED_NOT_ADMIN}_{entry_id}")
    ir.async_delete_issue(hass, DOMAIN, f"{_EMBED_UNSUPPORTED}_{entry_id}")
