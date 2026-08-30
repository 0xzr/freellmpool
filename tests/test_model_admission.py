"""Evidence-gated model additions for the current release."""

from __future__ import annotations

from pathlib import Path

from freellmpool.config import load_catalog

ROOT = Path(__file__).resolve().parents[1]


def test_groq_qwen38_is_enabled_only_as_a_preview_exact_pin():
    groq = next(provider for provider in load_catalog() if provider.id == "groq")
    model = groq.model("qwen/qwen3.8-27b")

    assert model.enabled is True
    assert model.auto is False
    assert model.rpd == 1_000
    assert model.context == 131_042


def test_groq_qwen38_public_docs_preserve_the_observed_admission_evidence():
    audit = (ROOT / "docs" / "MODEL_ACTIVITY_AUDIT_2026-08-29.md").read_text(
        encoding="utf-8"
    )
    page = (ROOT / "docs" / "free-groq-api.html").read_text(encoding="utf-8")

    for surface in (audit, page):
        assert "qwen/qwen3.8-27b" in surface
        assert "three" in surface.lower()
        assert "streaming" in surface.lower()
        assert "tool" in surface.lower()
        assert "pin-only" in surface.lower()
        assert "preview" in surface.lower()
