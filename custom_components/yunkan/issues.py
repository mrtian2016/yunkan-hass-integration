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
