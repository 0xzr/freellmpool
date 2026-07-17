from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ci_enforces_repository_coverage_floor() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    match = re.search(r"--cov-fail-under=(\d+)", workflow)

    assert match is not None, "CI must enforce an explicit coverage floor"
    assert int(match.group(1)) >= 80


def test_dependabot_covers_project_supply_chains() -> None:
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

    for ecosystem in ("pip", "github-actions", "docker"):
        assert f'package-ecosystem: "{ecosystem}"' in config


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
