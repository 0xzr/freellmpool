from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from freellmpool.models import Reply

ROOT = Path(__file__).resolve().parents[1]


def _load_vetter():
    path = ROOT / "scripts" / "vet_catalog.py"
    spec = importlib.util.spec_from_file_location("vet_catalog", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _provider(provider_id: str):
    return SimpleNamespace(
        id=provider_id,
        adapter="openai",
        base_url="https://example.test/v1",
        api_key=lambda env: None,
    )


def test_discovery_filters_paid_aggregator_routes(monkeypatch) -> None:
    vetter = _load_vetter()
    monkeypatch.setattr(
        vetter,
        "_http_get",
        lambda url, headers: {
            "data": [
                {"id": "vendor/paid"},
                {"id": "vendor/model:free"},
                {"id": "openrouter/free"},
                {"id": "kilo-auto/free"},
                {"id": "hy3-free"},
            ]
        },
    )

    assert vetter.list_live_models(_provider("kilo"), {}) == [
        "vendor/model:free",
        "openrouter/free",
        "kilo-auto/free",
    ]
    assert vetter.list_live_models(_provider("opencode"), {}) == ["hy3-free"]


def test_pollinations_discovery_includes_canonical_name_and_aliases(monkeypatch) -> None:
    vetter = _load_vetter()
    monkeypatch.setattr(
        vetter,
        "_http_get",
        lambda url, headers: [
            {
                "name": "openai-fast",
                "aliases": ["openai", "gpt-oss", "bad`alias", 42],
            }
        ],
    )

    assert vetter.list_live_models(_provider("pollinations"), {}) == [
        "gpt-oss",
        "openai",
        "openai-fast",
    ]


def test_ping_model_rejects_empty_http_success(monkeypatch) -> None:
    vetter = _load_vetter()
    calls = 0

    def empty_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        return Reply("", "demo", "model", {})

    monkeypatch.setattr(vetter.flp_client, "call", empty_call)
    monkeypatch.setattr(vetter.time, "sleep", lambda seconds: None)

    result = vetter.ping_model(_provider("demo"), "model", {}, timeout=1.0)

    assert calls == 3
    assert result["ok"] is False
    assert result["status"] == 200
    assert result["classification"] == "empty"


def test_failure_classification_recognizes_retired_models() -> None:
    vetter = _load_vetter()

    assert vetter._classify(410, "model reached end of life") == "dead"
    assert vetter._classify(400, "Model 'old' is currently unavailable") == "dead"
    assert vetter._classify(400, "Unsupported model (model=old)") == "dead"
    assert vetter._classify(400, "Unknown model: old") == "dead"
    assert vetter._classify(402, "payment method required") == "other"


def test_non_chat_detection_catches_safety_and_reward_models() -> None:
    vetter = _load_vetter()

    assert not vetter._looks_chat("nvidia/nemotron-3.5-content-safety")
    assert not vetter._looks_chat("nvidia/nemotron-4-340b-reward")
    assert not vetter._looks_chat("cohere-transcribe-03-2026")
    assert not vetter._looks_chat("google/diffusiongemma-26b-a4b-it")
    assert not vetter._looks_chat("nvidia/ising-calibration-1-35b-a3b")
    assert not vetter._looks_chat("ppl")
