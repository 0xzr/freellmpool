from __future__ import annotations

import importlib.util
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call

import pytest

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
    assert counts.enabled_chat_models == 177
    assert counts.cataloged_chat_models == 431
    assert release_ready.metadata_errors(ROOT) == []


def test_release_output_dir_rejects_existing_content_without_deleting_it(tmp_path):
    release_ready = _load_script("check_release_ready")
    root = tmp_path / "repo"
    root.mkdir()
    output = tmp_path / "existing-output"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("user data", encoding="utf-8")

    with pytest.raises(ValueError, match="must be empty"):
        release_ready._prepare_dist_dir(root, output)

    assert sentinel.read_text(encoding="utf-8") == "user data"


def test_release_output_dir_rejects_repository_and_ancestor_paths(tmp_path):
    release_ready = _load_script("check_release_ready")
    root = tmp_path / "repo"
    root.mkdir()

    for unsafe in (root, tmp_path):
        with pytest.raises(ValueError, match="repository root or an ancestor"):
            release_ready._prepare_dist_dir(root, unsafe)


def test_release_output_dir_accepts_new_or_empty_dedicated_directory(tmp_path):
    release_ready = _load_script("check_release_ready")
    root = tmp_path / "repo"
    root.mkdir()
    existing_empty = tmp_path / "existing-empty"
    existing_empty.mkdir()
    new_output = tmp_path / "new-output"

    assert release_ready._prepare_dist_dir(root, existing_empty) == existing_empty.resolve()
    assert release_ready._prepare_dist_dir(root, new_output) == new_output.resolve()
    assert new_output.is_dir()


def test_docker_smoke_checks_exact_version_inside_image(monkeypatch):
    release_ready = _load_script("check_release_ready")
    commands: list[list[str]] = []

    monkeypatch.setattr(release_ready.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        release_ready,
        "_run",
        lambda command, *, cwd: commands.append(command),
    )

    release_ready.docker_smoke("example.invalid/freellmpool:0.12.1", "0.12.1")

    assert call(
        [
            "/usr/bin/docker",
            "run",
            "--rm",
            "--entrypoint",
            "python",
            "example.invalid/freellmpool:0.12.1",
            "-c",
            "import freellmpool; assert freellmpool.__version__ == '0.12.1', "
            "freellmpool.__version__",
        ]
    ) in [call(command) for command in commands]


def test_public_count_claims_match_catalog():
    result = subprocess.run(
        [sys.executable, "scripts/check-counts"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_count_gate_covers_translations_assets_and_bundled_plugin():
    namespace = runpy.run_path(str(ROOT / "scripts" / "check-counts"))
    surfaces = {relative for relative, _template in namespace["REQUIRED_SURFACES"]}

    assert {
        "README.es.md",
        "assets/demo.svg",
        "assets/social-preview.svg",
        "assets/tokenmax-results.svg",
        "docs/free-alternative-to-openrouter.html",
        "docs/free-claude-api.html",
        "plugins/llm-freellmpool/README.md",
        "plugins/llm-freellmpool/pyproject.toml",
    } <= surfaces


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


def test_public_count_drift_detects_provider_claim_with_multiple_adjectives(tmp_path):
    namespace = runpy.run_path(str(ROOT / "scripts" / "check-counts"))
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    (tmp_path / "FAQ.md").write_text("", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "stale.html").write_text(
        "<p>The proxy spans 24 pooled free providers.</p>\n",
        encoding="utf-8",
    )
    counts = SimpleNamespace(
        providers=22,
        enabled_chat_models=177,
        cataloged_chat_models=431,
    )

    errors = namespace["_check_public_drift"](tmp_path, counts)

    assert any("provider count drift: 24 pooled free providers" in error for error in errors)


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


def test_first_party_provider_claim_is_not_exempted_by_openrouter_context(tmp_path):
    namespace = runpy.run_path(str(ROOT / "scripts" / "check-counts"))
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    (tmp_path / "FAQ.md").write_text("", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "free-alternative-to-openrouter.html").write_text(
        "<p>You can point it at OpenRouter's free models as one pooled provider.</p>\n"
        "<h3>Can freellmpool use OpenRouter too?</h3>\n"
        "<p>Yes — OpenRouter is one of the 24 providers freellmpool can pool, "
        "so its free models become part of the failover pool.</p>\n",
        encoding="utf-8",
    )
    counts = SimpleNamespace(
        providers=22,
        enabled_chat_models=177,
        cataloged_chat_models=431,
    )

    errors = namespace["_check_public_drift"](tmp_path, counts)

    assert any("provider count drift: 24 providers" in error for error in errors)


def test_openrouter_external_model_counts_remain_exempt(tmp_path):
    namespace = runpy.run_path(str(ROOT / "scripts" / "check-counts"))
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    (tmp_path / "FAQ.md").write_text("", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "comparison.md").write_text(
        "OpenRouter's catalog has 999 enabled chat routes and 999 cataloged models.\n",
        encoding="utf-8",
    )
    counts = SimpleNamespace(
        providers=22,
        enabled_chat_models=177,
        cataloged_chat_models=431,
    )

    assert namespace["_check_public_drift"](tmp_path, counts) == []


def test_proxy_stress_script_tiny_profile():
    stress_proxy = _load_script("stress_proxy")

    assert stress_proxy.run_stress(requests=24, concurrency=4, json_output=True) == 0
