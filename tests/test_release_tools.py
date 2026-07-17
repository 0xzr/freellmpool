from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

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
    assert counts.providers == 22
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


def test_proxy_stress_script_tiny_profile():
    stress_proxy = _load_script("stress_proxy")

    assert stress_proxy.run_stress(requests=24, concurrency=4, json_output=True) == 0
