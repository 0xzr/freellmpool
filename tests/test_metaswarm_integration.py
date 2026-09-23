from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "integrations" / "metaswarm" / "freellmpool-review-adapter.sh"
README = ROOT / "integrations" / "metaswarm" / "README.md"


def _base_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["HOME"] = str(tmp_path / "home")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg-config")
    env["XDG_DATA_HOME"] = str(tmp_path / "xdg-data")
    env["XDG_CACHE_HOME"] = str(tmp_path / "xdg-cache")
    env["FREELLMPOOL_CONFIG_FILE"] = str(tmp_path / "empty-config.toml")
    env["FREELLMPOOL_KEYS_PATH"] = str(tmp_path / "empty-keys.json")
    env["METASWARM_LOG_DIR"] = str(tmp_path / "logs")
    # The adapter resolves its helper interpreter through FREELLMPOOL_PYTHON
    # (then the freellmpool sibling python3, then $VIRTUAL_ENV, then PATH
    # python3). Pin the test interpreter so a pre-3.11 PATH python3 on the host
    # cannot flip these tests into a different failure mode than the ones they
    # assert.
    env["FREELLMPOOL_PYTHON"] = sys.executable
    for name in ("MISTRAL_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY"):
        env.pop(name, None)
    (tmp_path / "home").mkdir()
    (tmp_path / "xdg-config").mkdir()
    (tmp_path / "xdg-data").mkdir()
    (tmp_path / "xdg-cache").mkdir()
    (tmp_path / "empty-config.toml").write_text("# empty test config\n", encoding="utf-8")
    (tmp_path / "empty-keys.json").write_text("{}\n", encoding="utf-8")
    return env


def _dirty_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "example.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "example.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)
    (repo / "example.txt").write_text("after\n", encoding="utf-8")
    return repo


def _review_files(tmp_path: Path) -> tuple[Path, Path]:
    spec = tmp_path / "spec.md"
    rubric = tmp_path / "rubric.md"
    spec.write_text("Return PASS when the diff has no secret.\n", encoding="utf-8")
    rubric.write_text("Check for secrets only. Blocking issues require FAIL.\n", encoding="utf-8")
    return spec, rubric


def _fake_freellmpool(tmp_path: Path, body: str) -> Path:
    fake = tmp_path / "freellmpool-fake"
    fake.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    fake.chmod(0o755)
    return fake


def test_metaswarm_adapter_shell_syntax() -> None:
    result = subprocess.run(["bash", "-n", str(ADAPTER)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_metaswarm_adapter_no_key_review_fails_closed(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    repo = _dirty_repo(tmp_path)
    spec, rubric = _review_files(tmp_path)
    fake_log = tmp_path / "fake-called.log"
    env["FREELLMPOOL_CMD"] = str(
        _fake_freellmpool(
            tmp_path,
            f'printf "%s\\n" "$*" >> "{fake_log}"\nexit 99',
        )
    )

    result = subprocess.run(
        [
            str(ADAPTER),
            "review",
            "--worktree",
            str(repo),
            "--rubric-file",
            str(rubric),
            "--spec-file",
            str(spec),
            "--timeout",
            "20",
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["tool"] == "freellmpool"
    assert payload["command"] == "review"
    assert payload["exit_code"] == 2
    assert payload["error_type"] == "auth_missing"
    assert "MISTRAL_API_KEY" in payload["raw_log"]
    assert "NVIDIA_API_KEY" in payload["raw_log"]
    assert "OPENROUTER_API_KEY" in payload["raw_log"]
    assert "secret" not in payload["raw_log"].lower()
    assert not fake_log.exists()


def test_metaswarm_adapter_tool_not_installed(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["FREELLMPOOL_CMD"] = str(tmp_path / "missing-freellmpool")
    repo = _dirty_repo(tmp_path)
    spec, rubric = _review_files(tmp_path)

    result = subprocess.run(
        [
            str(ADAPTER),
            "review",
            "--worktree",
            str(repo),
            "--rubric-file",
            str(rubric),
            "--spec-file",
            str(spec),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_type"] == "tool_not_installed"
    assert payload["exit_code"] == 127


def test_metaswarm_adapter_health_empty_config_unavailable(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(ADAPTER), "health"],
        text=True,
        capture_output=True,
        env=_base_env(tmp_path),
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "unavailable"
    assert payload["auth_valid"] is False
    assert payload["strong_provider_count"] == 0


def test_metaswarm_adapter_redacts_provider_error_logs(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["MISTRAL_API_KEY"] = "redaction-test-key"
    env["FREELLMPOOL_REVIEW_MODE"] = "ask"
    env["FREELLMPOOL_STRONG_PROVIDERS"] = "mistral"
    env["FREELLMPOOL_CMD"] = str(
        _fake_freellmpool(
            tmp_path,
            """
if [[ "${1:-}" == "--version" ]]; then
  printf 'freellmpool fake\\n'
  exit 0
fi
printf 'Authorization: Bearer redaction-test-key\\n' >&2
printf 'MISTRAL_API_KEY=redaction-test-key\\n' >&2
printf 'api_key=redaction-test-key\\n' >&2
exit 1
""",
        )
    )
    repo = _dirty_repo(tmp_path)
    spec, rubric = _review_files(tmp_path)

    result = subprocess.run(
        [
            str(ADAPTER),
            "review",
            "--worktree",
            str(repo),
            "--rubric-file",
            str(rubric),
            "--spec-file",
            str(spec),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_type"] == "auth_expired"
    assert "redaction-test-key" not in payload["raw_log"]
    assert "Bearer [REDACTED]" in payload["raw_log"]
    assert "MISTRAL_API_KEY=[REDACTED]" in payload["raw_log"]
    assert "api_key=[REDACTED]" in payload["raw_log"]
    persisted_log = Path(payload["raw_log_path"]).read_text(encoding="utf-8")
    assert "redaction-test-key" not in persisted_log


def test_metaswarm_adapter_invalid_provider_key_fails_closed(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["MISTRAL_API_KEY"] = "bad key value"
    env["FREELLMPOOL_STRONG_PROVIDERS"] = "mistral"
    fake_log = tmp_path / "fake-called.log"
    env["FREELLMPOOL_CMD"] = str(
        _fake_freellmpool(
            tmp_path,
            f'printf "%s\\n" "$*" >> "{fake_log}"\nexit 99',
        )
    )
    repo = _dirty_repo(tmp_path)
    spec, rubric = _review_files(tmp_path)

    result = subprocess.run(
        [
            str(ADAPTER),
            "review",
            "--worktree",
            str(repo),
            "--rubric-file",
            str(rubric),
            "--spec-file",
            str(spec),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_type"] == "auth_missing"
    assert "MISTRAL_API_KEY" in payload["raw_log"]
    assert "bad key value" not in payload["raw_log"]
    assert not fake_log.exists()


def test_metaswarm_adapter_partial_strong_config_fails_closed(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["MISTRAL_API_KEY"] = "mistral-test-key"
    env["FREELLMPOOL_STRONG_PROVIDERS"] = "mistral,nvidia"
    env["FREELLMPOOL_STRONG_MODELS"] = "mistral/mistral-large-latest,nvidia/test-model"
    fake_log = tmp_path / "fake-called.log"
    env["FREELLMPOOL_CMD"] = str(
        _fake_freellmpool(
            tmp_path,
            f'printf "%s\\n" "$*" >> "{fake_log}"\nexit 99',
        )
    )
    repo = _dirty_repo(tmp_path)
    spec, rubric = _review_files(tmp_path)

    result = subprocess.run(
        [
            str(ADAPTER),
            "review",
            "--worktree",
            str(repo),
            "--rubric-file",
            str(rubric),
            "--spec-file",
            str(spec),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_type"] == "auth_missing"
    assert "nvidia" in payload["raw_log"]
    assert not fake_log.exists()


def test_metaswarm_adapter_empty_diff_fails_closed(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["MISTRAL_API_KEY"] = "mistral-test-key"
    env["FREELLMPOOL_STRONG_PROVIDERS"] = "mistral"
    env["FREELLMPOOL_STRONG_MODELS"] = "mistral/mistral-large-latest"
    fake_log = tmp_path / "fake-called.log"
    env["FREELLMPOOL_CMD"] = str(
        _fake_freellmpool(
            tmp_path,
            f'printf "%s\\n" "$*" >> "{fake_log}"\nexit 99',
        )
    )
    repo = _dirty_repo(tmp_path)
    subprocess.run(["git", "checkout", "--", "example.txt"], cwd=repo, check=True)
    spec, rubric = _review_files(tmp_path)

    result = subprocess.run(
        [
            str(ADAPTER),
            "review",
            "--worktree",
            str(repo),
            "--rubric-file",
            str(rubric),
            "--spec-file",
            str(spec),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_type"] == "empty_diff"
    assert "No git diff was captured" in payload["raw_log"]
    assert not fake_log.exists()


def test_metaswarm_adapter_synthesis_failure_fails_closed(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["MISTRAL_API_KEY"] = "mistral-test-key"
    env["FREELLMPOOL_STRONG_PROVIDERS"] = "mistral"
    env["FREELLMPOOL_STRONG_MODELS"] = "mistral/mistral-large-latest"
    count_file = tmp_path / "fake-count.txt"
    env["FREELLMPOOL_CMD"] = str(
        _fake_freellmpool(
            tmp_path,
            f"""
count=0
if [[ -f "{count_file}" ]]; then
  count="$(cat "{count_file}")"
fi
count=$((count + 1))
printf '%s\\n' "$count" > "{count_file}"
if [[ "$count" -eq 1 ]]; then
  printf '{{"verdict":"PASS","findings":[],"summary":"review ok"}}\\n'
  exit 0
fi
printf 'context length exceeded during synthesis\\n' >&2
exit 1
""",
        )
    )
    repo = _dirty_repo(tmp_path)
    spec, rubric = _review_files(tmp_path)

    result = subprocess.run(
        [
            str(ADAPTER),
            "review",
            "--worktree",
            str(repo),
            "--rubric-file",
            str(rubric),
            "--spec-file",
            str(spec),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_type"] == "context_too_large"
    assert "synthesis unavailable" in payload["raw_log"]
    assert count_file.read_text(encoding="utf-8").strip() == "2"


def test_metaswarm_adapter_implement_is_unsupported(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(ADAPTER), "implement", "--attempt", "2"],
        text=True,
        capture_output=True,
        env=_base_env(tmp_path),
        check=False,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["tool"] == "freellmpool"
    assert payload["command"] == "implement"
    assert payload["attempt"] == 2
    assert payload["exit_code"] == 2
    assert payload["error_type"] == "unsupported_role"
    assert "review-only" in payload["raw_log"]


def test_metaswarm_docs_are_linked() -> None:
    integrations = (ROOT / "docs" / "INTEGRATIONS.md").read_text(encoding="utf-8")
    agents = (ROOT / "docs" / "AGENTS.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    llms = (ROOT / "docs" / "llms.txt").read_text(encoding="utf-8")
    adapter_text = ADAPTER.read_text(encoding="utf-8")
    integration_readme = README.read_text(encoding="utf-8")

    assert "integrations/metaswarm" in integrations
    assert "metaswarm" in agents.lower()
    assert "metaswarm" in readme.lower()
    assert "metaswarm" in llms.lower()
    assert "auth_missing" in adapter_text
    assert "review-only" in integration_readme
    assert ".metaswarm/external-tools.yaml" in integration_readme


def test_metaswarm_adapter_uses_freellmpool_python_for_helpers(tmp_path: Path) -> None:
    """Helpers must run under FREELLMPOOL_PYTHON, not the host's PATH python3."""
    env = _base_env(tmp_path)
    marker = tmp_path / "interpreter-marker.txt"
    env["FREELLMPOOL_CMD"] = str(
        _fake_freellmpool(
            tmp_path,
            "exit 99",
        )
    )
    env["FREELLMPOOL_PYTHON"] = str(
        _fake_freellmpool(
            tmp_path,
            f'printf "called\\n" >> "{marker}"\nexec "{sys.executable}" "$@"',
        )
    )

    result = subprocess.run(
        [str(ADAPTER), "health"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    # The wrapper execs a real interpreter, so helpers still work — but the
    # marker proves the adapter honored FREELLMPOOL_PYTHON instead of silently
    # using the host's PATH python3.
    assert payload["status"] == "unavailable"
    assert payload["auth_valid"] is False
    assert marker.exists()


def test_metaswarm_sibling_python_wins_over_unrelated_virtualenv(tmp_path: Path) -> None:
    """The freellmpool sibling interpreter beats an unrelated active virtualenv."""
    env = _base_env(tmp_path)
    env["MISTRAL_API_KEY"] = "mistral-test-key"
    env["FREELLMPOOL_STRONG_PROVIDERS"] = "mistral"
    fake_log = tmp_path / "fake-called.log"
    env["FREELLMPOOL_STRONG_MODELS"] = "mistral/mistral-large-latest"
    # An "unrelated" active virtualenv: real venv bin layout, but freellmpool
    # is NOT importable there. Running the host interpreter with -I -S drops
    # site-packages and ignores PYTHONPATH/PYTHON* vars while keeping the
    # stdlib (tomllib, sys) working — a deterministic stand-in for a real venv
    # without the package, on any host, even where the host python has the
    # package installed.
    unrelated_venv = tmp_path / "unrelated-venv"
    venv_bin = unrelated_venv / "bin"
    venv_bin.mkdir(parents=True)
    venv_python = venv_bin / "python3"
    venv_python.write_text(f"#!/bin/sh\nexec '{sys.executable}' -I -S \"$@\"\n")
    venv_python.chmod(0o755)
    env["VIRTUAL_ENV"] = str(unrelated_venv)
    # Tier 1 (FREELLMPOOL_PYTHON) is pinned by _base_env; this test exercises
    # the auto-resolution tiers, so the explicit override must be absent.
    env.pop("FREELLMPOOL_PYTHON", None)
    env["FREELLMPOOL_CMD"] = str(
        _fake_freellmpool(
            tmp_path,
            f'printf "%s\\n" "$*" >> "{fake_log}"\nexit 99',
        )
    )
    # Capable python3 sitting next to the configured freellmpool executable.
    sibling_python = tmp_path / "python3"
    sibling_python.write_text(f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n")
    sibling_python.chmod(0o755)

    repo = _dirty_repo(tmp_path)
    spec, rubric = _review_files(tmp_path)

    result = subprocess.run(
        [
            str(ADAPTER),
            "review",
            "--worktree",
            str(repo),
            "--rubric-file",
            str(rubric),
            "--spec-file",
            str(spec),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    # Under the buggy VIRTUAL_ENV-first order the incapable venv python is
    # picked, the helper reports -1, and review fails helper_unavailable
    # before any freellmpool call. Under the sibling-first order the capable
    # sibling resolves the helper, so the fake CLI is actually invoked and the
    # remaining failure is the tool-level one this harness induces (exit 99).
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_type"] == "tool_crash"
    assert fake_log.exists()


def test_metaswarm_adapter_reports_incompatible_interpreter(tmp_path: Path) -> None:
    """A helper interpreter that cannot import the project must fail clearly."""
    env = _base_env(tmp_path)
    env["MISTRAL_API_KEY"] = "mistral-test-key"
    env["FREELLMPOOL_STRONG_PROVIDERS"] = "mistral"
    fake_log = tmp_path / "fake-called.log"
    env["FREELLMPOOL_CMD"] = str(
        _fake_freellmpool(
            tmp_path,
            f'printf "%s\\n" "$*" >> "{fake_log}"\nexit 99',
        )
    )
    env["FREELLMPOOL_PYTHON"] = str(
        _fake_freellmpool(
            tmp_path,
            "exit 1",
        )
    )
    repo = _dirty_repo(tmp_path)
    spec, rubric = _review_files(tmp_path)

    result = subprocess.run(
        [
            str(ADAPTER),
            "review",
            "--worktree",
            str(repo),
            "--rubric-file",
            str(rubric),
            "--spec-file",
            str(spec),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_type"] == "helper_unavailable"
    assert "helper interpreter" in payload["raw_log"]
    assert not fake_log.exists()
