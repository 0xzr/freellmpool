"""Tests for the role-preset registry used by the CLI."""

from __future__ import annotations

from freellmpool.roles import ROLE_SPECS, format_roles, get_role, valid_roles

EXPECTED_ROLES = {
    "coder",
    "critic",
    "summarizer",
    "long-context",
    "cheap",
    "conserve",
    "fast",
    "second-opinion",
    "grounded-reader",
}


def test_registry_has_initial_roles():
    names = {role.name for role in ROLE_SPECS}
    assert EXPECTED_ROLES <= names


def test_valid_roles_matches_registry():
    assert set(valid_roles()) == {role.name for role in ROLE_SPECS}


def test_get_role_returns_spec():
    coder = get_role("coder")
    assert coder is not None
    assert coder.routing == "quality"
    assert coder.max_tokens is not None
    assert coder.system_prefix is not None


def test_get_role_case_insensitive():
    assert get_role("CODER") == get_role("coder")


def test_get_role_unknown_returns_none():
    assert get_role("not-a-role") is None
    assert get_role("") is None


def test_format_roles_includes_key_roles():
    out = format_roles()
    for name in ("coder", "critic", "cheap", "conserve", "second-opinion"):
        assert name in out


def test_format_roles_includes_routing_hints():
    out = format_roles()
    assert "routing=quality" in out
    assert "routing=spread" in out
    assert "routing=fast" in out


def test_coder_role_values():
    coder = get_role("coder")
    assert coder is not None
    assert coder.routing == "quality"
    assert coder.max_tokens == 2048
    assert "programmer" in coder.system_prefix.lower()


def test_critic_role_low_temperature():
    critic = get_role("critic")
    assert critic is not None
    assert critic.temperature is not None
    assert critic.temperature < 0.5


def test_fast_role_routing():
    fast = get_role("fast")
    assert fast is not None
    assert fast.routing == "fast"


def test_conserve_role_uses_quota_conscious_defaults():
    conserve = get_role("conserve")
    assert conserve is not None
    assert conserve.routing == "spread"
    assert conserve.max_tokens == 512


def test_second_opinion_role_uses_panel_defaults():
    role = get_role("second-opinion")
    assert role is not None
    assert role.routing == "quality"
    assert role.max_tokens == 512
    assert role.system_prefix is not None


def test_grounded_reader_role_declares_explicit_task_hint():
    role = get_role("grounded-reader")
    assert role is not None
    assert role.routing == "quality"
    assert role.task == "grounded-reading"
    assert "faithful" in role.system_prefix.lower()
