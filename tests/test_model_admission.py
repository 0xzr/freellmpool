"""Evidence-gated model additions for the current release."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from freellmpool.config import load_catalog
from freellmpool.conformance import target_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def test_groq_qwen38_is_enabled_only_as_a_preview_exact_pin():
    groq = next(provider for provider in load_catalog() if provider.id == "groq")
    model = groq.model("qwen/qwen3.8-27b")

    assert model.enabled is True
    assert model.auto is False
    assert model.rpd == 1_000
    assert model.context == 131_042


def test_groq_qwen38_machine_readable_admission_evidence_is_exact_and_secret_free():
    path = ROOT / "docs" / "evidence" / "groq-qwen3.8-27b-admission-2026-08-29.json"
    raw = path.read_text(encoding="utf-8")
    evidence = json.loads(raw)
    groq = next(provider for provider in load_catalog() if provider.id == "groq")

    assert set(evidence) == {
        "schema_version",
        "canary_contract",
        "verified_on",
        "target",
        "runs",
    }
    assert evidence["schema_version"] == 1
    assert evidence["canary_contract"] == "freellmpool-conformance-v1"
    assert date.fromisoformat(evidence["verified_on"]) == date(2026, 8, 29)
    assert evidence["target"] == {
        "provider_id": "groq",
        "adapter": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "qwen/qwen3.8-27b",
        "fingerprint_sha256": target_fingerprint(groq, "qwen/qwen3.8-27b"),
    }

    runs = evidence["runs"]
    assert len(runs) == 5
    assert [run["sequence"] for run in runs] == [1, 2, 3, 4, 5]
    assert all(
        set(run) == {"sequence", "feature", "status", "classification", "verified_on"}
        for run in runs
    )
    assert all(date.fromisoformat(run["verified_on"]) == date(2026, 8, 29) for run in runs)
    assert [(run["feature"], run["status"], run["classification"]) for run in runs] == [
        ("chat", "pass", "verified"),
        ("chat", "pass", "verified"),
        ("chat", "pass", "verified"),
        ("streaming", "pass", "verified"),
        ("tools", "unsupported", "unsupported"),
    ]
    assert len(raw.encode("utf-8")) < 4_096
    lowered = raw.lower()
    for forbidden in ("prompt", "response", "header", "secret", "token", "api_key"):
        assert forbidden not in lowered


def test_groq_qwen38_public_docs_link_the_machine_readable_admission_evidence():
    audit = (ROOT / "docs" / "MODEL_ACTIVITY_AUDIT_2026-08-29.md").read_text(
        encoding="utf-8"
    )
    page = (ROOT / "docs" / "free-groq-api.html").read_text(encoding="utf-8")
    evidence_name = "groq-qwen3.8-27b-admission-2026-08-29.json"

    for surface in (audit, page):
        assert "qwen/qwen3.8-27b" in surface
        assert "pin-only" in surface.lower()
        assert "preview" in surface.lower()
        assert evidence_name in surface
