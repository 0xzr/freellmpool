"""Shared routing-mode normalization across library/proxy/MCP."""

from __future__ import annotations

from freellmpool.proxy import _routing_and_model
from freellmpool.routing_modes import normalize_routing_mode, routing_override


def test_normalize_routing_mode_strips_and_falls_back():
    assert normalize_routing_mode(" Quality ", "fair") == "quality"
    assert normalize_routing_mode("bogus", "fast") == "fast"
    assert normalize_routing_mode("bogus", "also-bogus") == "fair"


def test_routing_override_treats_auto_as_default():
    assert routing_override("auto") is None
    assert routing_override(" spread ") == "spread"
    assert routing_override(" agent ") == "agent"
    assert routing_override("missing") is None


def test_proxy_model_alias_and_header_use_same_normalizer():
    assert _routing_and_model({}, "freellmpool/quality") == ("quality", "auto")
    assert _routing_and_model({}, "freellmpool/agent") == ("agent", "auto")
    assert _routing_and_model({"X-Freellmpool-Routing": " Fast "}, "auto") == (
        "fast",
        "auto",
    )
