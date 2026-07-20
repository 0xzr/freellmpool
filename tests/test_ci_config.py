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


def _coverage_policy() -> dict[str, int]:
    config = json.loads((ROOT / ".coverage-thresholds.json").read_text(encoding="utf-8"))
    thresholds = config["thresholds"]
    command = config["enforcement"]["command"]

    assert thresholds == {"lines": 80, "branches": 70}
    assert command == (
        "pytest --cov=freellmpool --cov-branch --cov-report=term-missing "
        "--cov-report=json:.coverage.json && "
        "python scripts/check_coverage.py .coverage.json"
    )
    assert config["enforcement"]["blockPRCreation"] is True
    assert config["enforcement"]["blockTaskCompletion"] is True
    return thresholds


def test_ci_enforces_repository_coverage_floor() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    policy = _coverage_policy()

    assert policy == {"lines": 80, "branches": 70}
    assert "pytest --cov=freellmpool --cov-branch" in workflow
    assert "--cov-report=json:.coverage.json" in workflow
    assert "python scripts/check_coverage.py .coverage.json" in workflow
    assert "--cov-fail-under" not in workflow
    assert ".coverage.json" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_ci_builds_and_smoke_tests_container() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert workflow.count("\n  docker-smoke:\n") == 1
    docker_job = workflow.split("\n  docker-smoke:\n", maxsplit=1)[1]
    for required in (
        "permissions:\n      contents: read",
        "docker build",
        "--entrypoint python",
        "sys.version_info[:2] == (3, 14)",
        "freellmpool.__version__",
        '"$image" --version',
        "os.getuid() != 0",
        "trap cleanup EXIT",
        "docker logs",
        "http://127.0.0.1:18080/healthz",
        'payload["status"] == "ok"',
    ):
        assert required in docker_job
    assert "docker/login-action" not in docker_job
    assert "push: true" not in docker_job


def test_ci_validates_opencode_packages_with_current_runtimes() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert workflow.count("\n  opencode-packages:\n") == 1
    job = workflow.split("\n  opencode-packages:\n", maxsplit=1)[1].split(
        "\n  docker-smoke:\n", maxsplit=1
    )[0]
    for required in (
        "permissions:\n      contents: read",
        "actions/setup-node@v6",
        'node-version: "24"',
        "oven-sh/setup-bun@v2",
        "npm@11.5.1",
        "node scripts/check_opencode_packages.mjs",
    ):
        assert required in job


def test_ci_strictly_checks_focused_helpers_without_recursive_debt() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert (
        "mypy --follow-imports=skip src/freellmpool/routing_modes.py "
        "src/freellmpool/catalog_validation.py src/freellmpool/_version.py "
        "src/freellmpool/readiness.py"
    ) in workflow


def test_opencode_publish_workflow_is_manual_protected_and_exact_sha() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "publish-opencode.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    for required in (
        "package:",
        "version:",
        "commit:",
        "environment: npm",
        "contents: read",
        "id-token: write",
        "runs-on: ubuntu-latest",
        "actions/checkout@v7",
        "actions/setup-node@v6",
        'node-version: "24"',
        "oven-sh/setup-bun@v2",
        "npm@11.5.1",
        "git rev-parse origin/main",
        "node scripts/check_opencode_packages.mjs",
        "secrets.NPM_TOKEN",
        "npm publish --access public --provenance",
        "npm view",
    ):
        assert required in workflow


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
