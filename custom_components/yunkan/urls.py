"""Pure helpers for building signed playback URLs.

Kept free of any Home Assistant imports so the URL logic can be unit-tested on
its own.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from .const import HLS_PATH, RTSP_PATH, RTSP_PORT_DEFAULT, WHEP_PATH


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


def pick_live(grant: dict[str, Any]) -> tuple[str, str, str]:
    """Return (app, stream, token) for the main live stream (no variant).

    RTSP carries any audio codec natively (G.711/AAC are both legal in RTP), so
    the RTSP direct path always uses the original live stream — no dependency on
    the AAC/Opus transcode variants.
    """
    live = grant.get("live") or {}
    return live.get("app", "live"), live.get("stream", ""), grant.get("token", "")


def build_rtsp_url(base_url: str, app: str, stream: str, token: str,
                   rtsp_port: int | None = None) -> str:
    """Build the direct RTSP URL for a stream (low-latency third-party path).

    Host comes from the HTTP base URL; the port is the server's RTSP port from
    the live-grant response (``rtsp_port``), defaulting to 23880 for older
    backends. Sub-second latency, no HLS segmentation/GOP dependency — the
    server validates the same signed token on RTSP DESCRIBE.
    """
    host = urlsplit(base_url).hostname or ""
    port = int(rtsp_port or RTSP_PORT_DEFAULT)
    path = RTSP_PATH.format(app=quote(app, safe=""), stream=quote(stream, safe=""))
    return f"rtsp://{host}:{port}{path}?{urlencode({'token': token})}"


def build_hls_url(base_url: str, app: str, stream: str, token: str) -> str:
    """Build the HLS playlist URL for a stream."""
    path = HLS_PATH.format(app=quote(app, safe=""), stream=quote(stream, safe=""))
    return f"{base_url}{path}?{urlencode({'token': token})}"


def build_whep_url(base_url: str, app: str, stream: str, token: str) -> str:
    """Build the WHEP signalling URL for a stream."""
    query = urlencode({"app": app, "stream": stream, "token": token})
    return f"{base_url}{WHEP_PATH}?{query}"
