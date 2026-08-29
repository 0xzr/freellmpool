from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import yaml

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
        "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020",
        'node-version: "24"',
        "oven-sh/setup-bun@0c5077e51419868618aeaa5fe8019c62421857d6",
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
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020",
        'node-version: "24"',
        "oven-sh/setup-bun@0c5077e51419868618aeaa5fe8019c62421857d6",
        "npm@11.5.1",
        "git rev-parse origin/main",
        "node scripts/check_opencode_packages.mjs",
        "secrets.NPM_TOKEN",
        "npm publish --access public --provenance",
        "npm view",
    ):
        assert required in workflow


def test_mcp_publish_workflow_is_manual_pinned_and_reproducible() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-mcp.yml").read_text(
        encoding="utf-8"
    )
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    jobs = yaml.safe_load(workflow)["jobs"]

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    for required in (
        "version:",
        "commit:",
        "environment: mcp-registry",
        "contents: read",
        "id-token: write",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "git merge-base --is-ancestor",
            "git cat-file -t",
        "refs/tags/v$RELEASE_VERSION^{commit}",
        "releases/tags/$release_tag",
        ".immutable",
        "mcp-publisher_linux_amd64.tar.gz",
        "ab128162b0616090b47cf245afe0a23f3ef08936fdce19074f5ba0a4469281ac",
        "mcp-publisher validate server.json",
        "mcp-publisher login github-oidc",
        "mcp-publisher publish server.json",
        "scripts/verify_mcp_registry.py server.json",
    ):
        assert required in workflow
    assert "mcp-manifest:" in ci
    assert "mcp-publisher_linux_amd64.tar.gz" in ci
    assert "ab128162b0616090b47cf245afe0a23f3ef08936fdce19074f5ba0a4469281ac" in ci
    assert "mcp-publisher validate server.json" in ci
    assert set(jobs) == {"publish", "verify"}
    assert jobs["verify"]["needs"] == "publish"
    assert jobs["publish"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert jobs["verify"]["permissions"] == {"contents": "read"}
    publish_text, verify_text = workflow.split("\n  verify:\n", maxsplit=1)
    assert "Detect an exact existing registry record" in publish_text
    assert "with exact-record recovery" in publish_text
    assert "steps.registry-preflight.outputs.complete != 'true'" in publish_text
    assert publish_text.count("scripts/verify_mcp_registry.py server.json") >= 2
    assert "publication failed without an exact recoverable registry record" in publish_text
    assert "scripts/verify_mcp_registry.py server.json" in verify_text


def test_llm_plugin_publish_workflow_recovers_only_matching_pypi_artifacts() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "publish-llm-plugin.yml"
    ).read_text(encoding="utf-8")
    jobs = yaml.safe_load(workflow)["jobs"]

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert set(jobs) == {"build", "publish", "verify"}
    assert jobs["publish"]["needs"] == "build"
    assert jobs["verify"]["needs"] == "publish"
    assert jobs["build"]["permissions"] == {"contents": "read"}
    assert jobs["publish"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert jobs["verify"]["permissions"] == {"contents": "read"}

    build_text, remainder = workflow.split("\n  publish:\n", maxsplit=1)
    publish_text, verify_text = remainder.split("\n  verify:\n", maxsplit=1)
    assert "refusing to overwrite" not in build_text
    assert "pypi.org/pypi/llm-freellmpool" not in build_text
    assert 'SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"' in build_text
    assert "export SOURCE_DATE_EPOCH" in build_text

    publish_steps = jobs["publish"]["steps"]
    checkout = next(step for step in publish_steps if step.get("name") == "Check out the exact release commit")
    assert checkout["uses"] == (
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    )
    assert checkout["with"] == {
        "ref": "${{ inputs.commit }}",
        "persist-credentials": False,
    }
    setup = next(step for step in publish_steps if step.get("name") == "Set up Python")
    assert setup["uses"] == (
        "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
    )
    revalidate = next(
        step
        for step in publish_steps
        if step.get("name")
        == "Revalidate immutable root release in the protected publish job"
    )
    assert 'git rev-parse HEAD)" = "$RELEASE_COMMIT"' in revalidate["run"]
    assert 'git merge-base --is-ancestor "$RELEASE_COMMIT" origin/main' in revalidate[
        "run"
    ]
    assert 'refs/tags/$root_tag^{commit}' in revalidate["run"]
    assert "git cat-file -t" in revalidate["run"]
    assert ".immutable" in revalidate["run"]
    download = next(
        step for step in publish_steps if step.get("name") == "Download the verified distributions"
    )
    assert download["uses"] == (
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
    )

    preflight = next(step for step in publish_steps if step.get("id") == "preflight")
    assert "scripts/verify_pypi_artifacts.py" in preflight["run"]
    assert "--mode subset" in preflight["run"]
    assert "--mode exact" in preflight["run"]
    assert 'upload_needed=false' in preflight["run"]
    assert 'upload_needed=true' in preflight["run"]

    auth = next(
        step
        for step in publish_steps
        if step.get("name") == "Validate the selected authentication mode"
    )
    publish = next(
        step for step in publish_steps if str(step.get("uses", "")).startswith("pypa/")
    )
    upload_guard = "steps.preflight.outputs.upload_needed == 'true'"
    assert upload_guard in auth["if"]
    assert upload_guard in publish["if"]
    assert publish["uses"] == (
        "pypa/gh-action-pypi-publish@ed0c53931b1dc9bd32cbe73a98c7f6766f8a527e"
    )
    assert "106e0b0b7c337fa67ed433972f777c6357f78598" not in workflow
    assert publish["with"]["skip-existing"] is True
    assert publish_text.index("--mode subset") < publish_text.index(
        "Validate the selected authentication mode"
    )
    assert publish_text.index("Validate the selected authentication mode") < (
        publish_text.index("pypa/gh-action-pypi-publish@")
    )

    assert "scripts/verify_pypi_artifacts.py" in verify_text
    assert "--mode exact" in verify_text
    assert "Fresh-install and smoke the registry wheel" in verify_text


def test_pages_deployment_is_gated_by_current_docs_metadata() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(
        encoding="utf-8"
    )

    for required in (
        "release:\n    types: [published]",
        "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
        'python-version: "3.14"',
        'pip install --disable-pip-version-check -e ".[dev]"',
        "scripts/check-counts",
        "scripts/check_docs.py docs",
        "scripts/check_release_ready.py --skip-build",
        "pytest tests/test_release_metadata.py tests/test_mcp_listings.py",
        "releases/tags/v$PROJECT_VERSION",
        "pypi.org/pypi/freellmpool/{sys.argv[1]}/json",
        "refs/tags/v$project_version^{commit}",
    ):
        assert required in workflow
    assert workflow.index("scripts/check-counts") < workflow.index(
        "actions/upload-pages-artifact@"
    )
    jobs = yaml.safe_load(workflow)["jobs"]
    assert jobs["validate"]["outputs"] == {
        "deploy_ready": "${{ steps.availability.outputs.deploy_ready }}"
    }
    assert jobs["validate"]["permissions"] == {"contents": "read"}
    assert jobs["deploy"]["needs"] == "validate"
    assert jobs["deploy"]["if"] == (
        "needs.validate.outputs.deploy_ready == 'true'"
    )
    assert jobs["deploy"]["permissions"] == {
        "id-token": "write",
        "pages": "write",
    }
    assert all("run" not in step for step in jobs["deploy"]["steps"])
    deploy_uses = [step["uses"] for step in jobs["deploy"]["steps"]]
    assert deploy_uses == [
        "actions/configure-pages@45bfe0192ca1faeb007ade9deae92b16b8254a0d",
        "actions/deploy-pages@cd2ce8fcbc39b97be8ca5fce6e763baed58fa128"
    ]


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
    match = re.search(
        r"^FROM python:(3\.\d+)-(?:alpine|slim)@sha256:([0-9a-f]{64})$",
        dockerfile,
        re.MULTILINE,
    )

    assert match is not None, "Dockerfile must use a digest-pinned official Python image"
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
