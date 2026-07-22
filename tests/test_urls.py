"""Tests for the URL / stream-variant helpers (no Home Assistant needed)."""

from __future__ import annotations

from custom_components.yunkan.urls import build_hls_url, build_whep_url, pick_stream


def test_pick_stream_prefers_variant() -> None:
    """A present variant with a stream + token wins over the main live stream."""
    grant = {
        "token": "MAIN",
        "live": {"app": "live", "stream": "cam01"},
        "aac_variant": {"app": "live", "stream": "cam01_aac", "token": "AAC"},
    }
    app, stream, token = pick_stream(grant, "aac_variant")
    assert (app, stream, token) == ("live", "cam01_aac", "AAC")


def test_pick_stream_falls_back_to_live() -> None:
    """A missing variant falls back to the main live stream + main token."""
    grant = {
        "token": "MAIN",
        "live": {"app": "live", "stream": "cam01"},
        "opus_variant": None,
    }
    app, stream, token = pick_stream(grant, "opus_variant")
    assert (app, stream, token) == ("live", "cam01", "MAIN")


def test_build_hls_url() -> None:
    """HLS URL uses the {app}/{stream}/hls.m3u8?token= shape."""
    url = build_hls_url("http://host:23406", "live", "cam01_aac", "T")
    assert url == "http://host:23406/live/cam01_aac/hls.m3u8?token=T"


def test_build_whep_url_has_three_query_params() -> None:
    """WHEP URL carries app, stream and token as query params."""
    url = build_whep_url("http://host:23406", "live", "cam01_opus", "T")
    assert url == "http://host:23406/index/api/whep?app=live&stream=cam01_opus&token=T"
