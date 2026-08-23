from __future__ import annotations

import re
from pathlib import Path

from freellmpool.agents import AGENTS, list_agents, render

ROOT = Path(__file__).resolve().parents[1]


def test_list_agents():
    out = list_agents()
    for a in ("codex", "aider", "cline", "continue", "cursor", "opencode", "hermes"):
        assert a in out


def test_render_hermes_custom_endpoint():
    out = render("hermes")
    assert out is not None
    assert "provider: custom" in out
    assert "default: quality" in out
    assert "http://localhost:8080/v1" in out


def test_render_known():
    out = render("aider")
    assert out is not None
    assert "openai/auto" in out
    assert "freellmpool proxy" in out


def test_render_unknown():
    assert render("bogus") is None


def test_all_agents_render():
    for name in AGENTS:
        assert render(name)


def test_agents_legacy_shape_is_preserved():
    rec = AGENTS["aider"]
    assert "label" in rec
    assert "steps" in rec
    assert "note" in rec
    assert any("freellmpool proxy" in step for step in rec["steps"])


def test_all_agent_keys_appear_in_supported_agent_list():
    out = list_agents()
    for name in AGENTS:
        assert name in out


def test_all_agent_keys_appear_in_integrations_guide():
    integrations = (ROOT / "docs" / "INTEGRATIONS.md").read_text(encoding="utf-8")
    match = re.search(
        r"^##\s+coding agents(?:\s*&\s*editors)?\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
        integrations,
        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    coding_agents_section = match.group("body").casefold()

    for name in AGENTS:
        assert name.casefold() in coding_agents_section


def test_agents_guide_tracks_current_release_surfaces():
    guide = (ROOT / "docs" / "AGENTS.md").read_text(encoding="utf-8")

    assert "Latest release: 0.12.0" in guide
    assert "Current main includes unreleased changes" not in guide
    assert "0.11.4" not in guide
    assert "Registry publication status: pending" in guide
    for marker in (
        "Hermes",
        "freellmpool/agent",
        "freellmpool/spread",
        "/livez",
        "/readyz",
        "/v1/providers",
        "/v1/models?ready=true",
        "opencode-freellmpool",
        "opencode-freellmpool-tui",
    ):
        assert marker in guide
