"""Helpers to extract human-useful attributes from Yunkan detection events.

The event ``extra`` field arrives as a JSON string. It carries the detail that
makes an event meaningful — the recognised face name, the licence plate, the
match similarity, zone/tripwire context — which this module surfaces as entity
attributes.
"""

from __future__ import annotations

import json
from typing import Any


def parse_extra(event: dict[str, Any]) -> dict[str, Any]:
    """Return the event ``extra`` as a dict (it is delivered as a JSON string)."""
    raw = event.get("extra")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _names(extra: dict[str, Any]) -> list[str]:
    """Collect recognised names from the top level and detection entries."""
    names: list[str] = []
    top = extra.get("name")
    if isinstance(top, str) and top:
        names.append(top)
    for det in extra.get("detections", []):
        if isinstance(det, dict) and det.get("name") and det["name"] not in names:
            names.append(det["name"])
    return names


def _plates(extra: dict[str, Any]) -> list[str]:
    """Collect licence plates from the top level and detection entries."""
    plates: list[str] = []
    top = extra.get("plate")
    if isinstance(top, str) and top:
        plates.append(top)
    for det in extra.get("detections", []):
        if isinstance(det, dict) and det.get("plate") and det["plate"] not in plates:
            plates.append(det["plate"])
    return plates


def event_attributes(event: dict[str, Any]) -> dict[str, Any]:
    """Build a rich attribute dict for a detection event.

    Includes the recognised face name, licence plate, match similarity, object
    labels, zone/tripwire context and any VLM summary — whichever apply.
    """
    extra = parse_extra(event)
    attrs: dict[str, Any] = {
        "event_id": event.get("id"),
        "event_type": event.get("event_type"),
        "confidence": event.get("confidence"),
        "event_time": event.get("event_time"),
    }

    names = _names(extra)
    if names:
        attrs["name"] = names[0] if len(names) == 1 else names
    if extra.get("similarity") is not None:
        attrs["similarity"] = extra["similarity"]

    plates = _plates(extra)
    if plates:
        attrs["plate"] = plates[0] if len(plates) == 1 else plates
        if extra.get("region"):
            attrs["plate_region"] = extra["region"]
        if extra.get("color"):
            attrs["plate_color"] = extra["color"]

    labels = [
        det.get("label")
        for det in extra.get("detections", [])
        if isinstance(det, dict) and det.get("label")
    ]
    if labels:
        attrs["objects"] = labels

    if extra.get("zone_name"):
        attrs["zone"] = extra["zone_name"]
    if event.get("direction"):
        attrs["direction"] = event["direction"]

    summary = event.get("summary_en") or event.get("summary_zh")
    if summary:
        attrs["summary"] = summary
    if event.get("title"):
        attrs["title"] = event["title"]

    return {key: value for key, value in attrs.items() if value is not None}
