from __future__ import annotations

from pathlib import Path

from freellmpool.agents import AGENTS, list_agents, render


def test_list_agents():
    out = list_agents()
    for a in ("codex", "aider", "cline", "continue", "cursor", "opencode"):
        assert a in out


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
    integrations = Path("docs/INTEGRATIONS.md").read_text(encoding="utf-8").lower()
    coding_agents_section = integrations.split("## coding agents & editors", 1)[1].split(
        "## chat uis", 1
    )[0]

    for name in AGENTS:
        assert name in coding_agents_section
