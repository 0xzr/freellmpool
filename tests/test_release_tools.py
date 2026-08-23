from __future__ import annotations

import importlib.util
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_release_ready_metadata_is_clean():
    release_ready = _load_script("check_release_ready")
    counts = release_ready.catalog_counts(ROOT)

    # The exact provider count is a release-copy tripwire: adding/removing a provider
    # should force a deliberate README/docs/server metadata update.
    assert counts.providers == 24
    assert counts.enabled_chat_models >= 200
    assert counts.cataloged_chat_models >= 300
    assert release_ready.metadata_errors(ROOT) == []


def test_public_count_claims_match_catalog():
    result = subprocess.run(
        [sys.executable, "scripts/check-counts"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_public_count_drift_detects_claim_wrapped_across_lines(tmp_path):
    namespace = runpy.run_path(str(ROOT / "scripts" / "check-counts"))
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    (tmp_path / "FAQ.md").write_text("", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "wrapped.md").write_text(
        "The catalog has 24 providers and 407\ncataloged chat models. Keyless start follows.\n",
        encoding="utf-8",
    )
    counts = SimpleNamespace(
        providers=24,
        enabled_chat_models=226,
        cataloged_chat_models=410,
    )

    errors = namespace["_check_public_drift"](tmp_path, counts)
    assert any("cataloged model bucket drift" in error for error in errors)


def test_public_count_drift_is_not_hidden_by_same_sentence_keyless_copy(tmp_path):
    namespace = runpy.run_path(str(ROOT / "scripts" / "check-counts"))
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    (tmp_path / "FAQ.md").write_text("", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "stale.md").write_text(
        "Current catalog: 24 providers, 225 enabled chat routes, 409 cataloged chat "
        "models; keyless start when available.\n",
        encoding="utf-8",
    )
    counts = SimpleNamespace(
        providers=24,
        enabled_chat_models=226,
        cataloged_chat_models=410,
    )

    errors = namespace["_check_public_drift"](tmp_path, counts)
    assert any("enabled route bucket drift" in error for error in errors)
    assert any("cataloged model bucket drift" in error for error in errors)


def test_proxy_stress_script_tiny_profile():
    stress_proxy = _load_script("stress_proxy")

    assert stress_proxy.run_stress(requests=24, concurrency=4, json_output=True) == 0
