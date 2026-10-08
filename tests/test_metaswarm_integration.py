from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def _shell_script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _logging_python(path: Path, marker: Path) -> Path:
    """A python3 stand-in that records its first argument, then runs the real one."""
    return _shell_script(path, f'printf "%s\\n" "$1" >> "{marker}"\nexec "{sys.executable}" "$@"')


def _python_without_freellmpool(path: Path) -> Path:
    """A python3 stand-in with the stdlib but without freellmpool importable.

    -I -S drops site-packages and ignores PYTHONPATH, so this works the same on
    hosts where the real interpreter has freellmpool installed.
    """
    return _shell_script(path, f'exec "{sys.executable}" -I -S "$@"')


def _run_review(env: dict[str, str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    repo = _dirty_repo(tmp_path)
    spec, rubric = _review_files(tmp_path)
    return subprocess.run(
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


def test_metaswarm_adapter_uses_freellmpool_python_for_provider_check(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    marker = tmp_path / "interpreter-marker.txt"
    env["FREELLMPOOL_CMD"] = str(_fake_freellmpool(tmp_path, "exit 99"))
    env["FREELLMPOOL_PYTHON"] = str(_logging_python(tmp_path / "override" / "python3", marker))

    result = subprocess.run(
        [str(ADAPTER), "health"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "unavailable"
    assert payload["auth_valid"] is False
    # Only the provider check runs under the resolved interpreter, once, reading
    # its script from stdin.
    assert marker.read_text().splitlines() == ["-"]


def test_metaswarm_shebang_python_wins_over_unrelated_virtualenv(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env.pop("FREELLMPOOL_PYTHON", None)
    env.pop("FREELLMPOOL_CMD", None)
    marker = tmp_path / "interpreter-marker.txt"
    tool_python = _logging_python(tmp_path / "tool-venv" / "bin" / "python", marker)
    bin_dir = tmp_path / "on-path"
    bin_dir.mkdir()
    tool = bin_dir / "freellmpool"
    tool.write_text(f"#!{tool_python}\nprint('freellmpool 0.0.0-test')\n", encoding="utf-8")
    tool.chmod(0o755)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["VIRTUAL_ENV"] = str(tmp_path / "unrelated-venv")
    _python_without_freellmpool(tmp_path / "unrelated-venv" / "bin" / "python3")

    result = subprocess.run(
        [str(ADAPTER), "health"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "could not run the provider check" not in result.stderr
    # Running the tool itself goes through the same interpreter with the script
    # path as $1; the provider check passes "-".
    assert "-" in marker.read_text().splitlines()


def test_metaswarm_symlinked_freellmpool_uses_python_beside_target(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env.pop("FREELLMPOOL_PYTHON", None)
    env.pop("VIRTUAL_ENV", None)
    marker = tmp_path / "interpreter-marker.txt"
    venv_bin = tmp_path / "pipx-venv" / "bin"
    _logging_python(venv_bin / "python3", marker)
    target = _shell_script(venv_bin / "freellmpool", "exit 99")
    link_dir = tmp_path / "local-bin"
    link_dir.mkdir()
    (link_dir / "freellmpool").symlink_to(target)
    env["FREELLMPOOL_CMD"] = str(link_dir / "freellmpool")

    result = subprocess.run(
        [str(ADAPTER), "health"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text().splitlines() == ["-"]


def test_metaswarm_sibling_python_wins_over_unrelated_virtualenv(tmp_path: Path) -> None:
    """The freellmpool sibling interpreter beats an unrelated active virtualenv."""
    env = _base_env(tmp_path)
    env["MISTRAL_API_KEY"] = "mistral-test-key"
    env["FREELLMPOOL_STRONG_PROVIDERS"] = "mistral"
    env["FREELLMPOOL_STRONG_MODELS"] = "mistral/mistral-large-latest"
    env.pop("FREELLMPOOL_PYTHON", None)
    env["VIRTUAL_ENV"] = str(tmp_path / "unrelated-venv")
    _python_without_freellmpool(tmp_path / "unrelated-venv" / "bin" / "python3")
    fake_log = tmp_path / "fake-called.log"
    env["FREELLMPOOL_CMD"] = str(
        _fake_freellmpool(
            tmp_path,
            f'printf "%s\\n" "$*" >> "{fake_log}"\nexit 99',
        )
    )
    # Capable python3 next to the configured freellmpool executable.
    _shell_script(tmp_path / "python3", f'exec "{sys.executable}" "$@"')

    result = _run_review(env, tmp_path)

    # Picking the virtualenv would fail helper_unavailable before any
    # freellmpool call. With the sibling, the provider check passes and the
    # fake CLI runs, so the remaining failure is the induced exit 99.
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_type"] == "tool_crash"
    assert fake_log.exists()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param('echo "broken interpreter" >&2\nexit 1', "broken interpreter", id="crashes"),
        pytest.param("exit 0", "could not run the provider check", id="prints-nothing"),
        pytest.param(None, "could not import freellmpool", id="lacks-freellmpool"),
    ],
)
def test_metaswarm_adapter_reports_unusable_helper_interpreter(
    tmp_path: Path, body: str | None, expected: str
) -> None:
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
    interpreter = tmp_path / "helper" / "python3"
    if body is None:
        _python_without_freellmpool(interpreter)
    else:
        _shell_script(interpreter, body)
    env["FREELLMPOOL_PYTHON"] = str(interpreter)

    result = _run_review(env, tmp_path)

    assert result.returncode == 1, result.stderr
    payload = json.loads(result.stdout)
    assert payload["error_type"] == "helper_unavailable"
    assert "helper interpreter" in payload["raw_log"]
    assert expected in payload["raw_log"]
    assert not fake_log.exists()
