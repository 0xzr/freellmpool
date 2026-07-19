from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ci_python_versions() -> list[str]:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    match = re.search(r"python-version:\s*\[([^\]]+)\]", workflow)

    assert match is not None, "CI must define a Python test matrix"
    return re.findall(r'["\'](3\.\d+)["\']', match.group(1))


def _coverage_floor() -> int:
    config = json.loads((ROOT / ".coverage-thresholds.json").read_text(encoding="utf-8"))
    floor = int(config["thresholds"]["lines"])

    assert f"--cov-fail-under={floor}" in config["enforcement"]["command"]
    assert config["enforcement"]["blockPRCreation"] is True
    assert config["enforcement"]["blockTaskCompletion"] is True
    return floor


def test_ci_enforces_repository_coverage_floor() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    match = re.search(r"--cov-fail-under=(\d+)", workflow)

    assert match is not None, "CI must enforce an explicit coverage floor"
    assert int(match.group(1)) == _coverage_floor()


def test_dependabot_covers_project_supply_chains() -> None:
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

    for ecosystem in ("pip", "github-actions", "docker"):
        assert f'package-ecosystem: "{ecosystem}"' in config


def test_dependabot_preserves_compatible_python_lower_bounds() -> None:
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    update_blocks = re.split(r"(?=^  - package-ecosystem:)", config, flags=re.MULTILINE)
    pip_block = next(
        block
        for block in update_blocks
        if re.search(r'^  - package-ecosystem:\s*["\']?pip["\']?\s*$', block, re.MULTILINE)
    )

    assert re.search(
        r'^    versioning-strategy:\s*["\']?increase-if-necessary["\']?\s*$',
        pip_block,
        re.MULTILINE,
    )


def test_supported_python_versions_match_ci() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    classifiers = pyproject["project"]["classifiers"]
    supported = {
        match.group(1)
        for classifier in classifiers
        if (match := re.fullmatch(r"Programming Language :: Python :: (3\.\d+)", classifier))
    }
    tested = _ci_python_versions()

    assert len(tested) == len(set(tested)), "CI Python matrix must not contain duplicates"
    assert set(tested) == supported
    assert "3.14" in supported


def test_container_uses_supported_python_314() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"^FROM python:(3\.\d+)-slim$", dockerfile, re.MULTILINE)

    assert match is not None, "Dockerfile must use a versioned official Python slim image"
    assert match.group(1) == "3.14"
    assert match.group(1) in _ci_python_versions()


def test_container_defaults_are_local_and_non_root() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "USER freellmpool" in dockerfile
    assert '"--allow-lan"' in dockerfile
    assert '"--allow-no-auth"' in dockerfile
    assert '"127.0.0.1:8080:8080"' in compose
    assert '"127.0.0.1:3000:8080"' in compose
    assert "ghcr.io/open-webui/open-webui:v0.10.2" in compose
    assert 'WEBUI_AUTH: "${WEBUI_AUTH:-true}"' in compose


def test_workflows_use_current_action_majors() -> None:
    workflows = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / ".github" / "workflows").glob("*.yml")
    )

    assert "actions/checkout@v6" not in workflows
    assert "actions/cache/restore@v5" not in workflows
    assert "actions/cache/save@v5" not in workflows
    assert "docker/login-action@v3" not in workflows
    assert "docker/metadata-action@v5" not in workflows
    assert "docker/build-push-action@v6" not in workflows
