"""Pure helpers for building signed playback URLs.

Kept free of any Home Assistant imports so the URL logic can be unit-tested on
its own.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from .const import HLS_PATH, WHEP_PATH


def pick_stream(grant: dict[str, Any], variant_key: str) -> tuple[str, str, str]:
    """Return (app, stream, token) for a variant, falling back to the main live stream.

    ``variant_key`` is ``"aac_variant"`` for HLS (audible on non-AAC cameras) or
    ``"opus_variant"`` for WHEP (WebRTC-native codec). When the variant is absent
    the main live stream and its token are used.
    """
    variant = grant.get(variant_key)
    if isinstance(variant, dict) and variant.get("stream") and variant.get("token"):
        return variant.get("app", "live"), variant["stream"], variant["token"]
    live = grant.get("live") or {}
    return live.get("app", "live"), live.get("stream", ""), grant.get("token", "")


def build_hls_url(base_url: str, app: str, stream: str, token: str) -> str:
    """Build the HLS playlist URL for a stream."""
    path = HLS_PATH.format(app=quote(app, safe=""), stream=quote(stream, safe=""))
    return f"{base_url}{path}?{urlencode({'token': token})}"


def build_whep_url(base_url: str, app: str, stream: str, token: str) -> str:
    """Build the WHEP signalling URL for a stream."""
    query = urlencode({"app": app, "stream": stream, "token": token})
    return f"{base_url}{WHEP_PATH}?{query}"
