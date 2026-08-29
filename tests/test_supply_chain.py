from __future__ import annotations

import importlib.util
import json
import re
import sys
import tomllib
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
SPEC = importlib.util.spec_from_file_location(
    "security_exceptions",
    ROOT / "scripts" / "security_exceptions.py",
)
assert SPEC is not None and SPEC.loader is not None
SECURITY_EXCEPTIONS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SECURITY_EXCEPTIONS
SPEC.loader.exec_module(SECURITY_EXCEPTIONS)
ExceptionPolicyError = SECURITY_EXCEPTIONS.ExceptionPolicyError
find_native_suppressions = SECURITY_EXCEPTIONS.find_native_suppressions
ids_for = SECURITY_EXCEPTIONS.ids_for
load_exceptions = SECURITY_EXCEPTIONS.load_exceptions


def _action_uses(value: object) -> list[str]:
    if isinstance(value, dict):
        found = [
            action
            for key, action in value.items()
            if key == "uses" and isinstance(action, str)
        ]
        for nested in value.values():
            found.extend(_action_uses(nested))
        return found
    if isinstance(value, list):
        found = []
        for nested in value:
            found.extend(_action_uses(nested))
        return found
    return []


def _unpinned_actions(workflows: list[Path]) -> list[str]:
    unpinned: list[str] = []
    for workflow in sorted(workflows):
        document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for action in _action_uses(document):
            if action.startswith("./"):
                continue
            if re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", action) is None:
                unpinned.append(f"{workflow.name}: {action}")
    return unpinned


def _action_manifests(root: Path) -> list[Path]:
    ignored = {
        ".git",
        ".venv",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "vendor",
        "venv",
    }
    manifests = {
        *root.glob(".github/workflows/*.yml"),
        *root.glob(".github/workflows/*.yaml"),
        *(
            path
            for name in ("action.yml", "action.yaml")
            for path in root.rglob(name)
            if not ignored.intersection(path.relative_to(root).parts)
        ),
    }
    queue = list(manifests)
    while queue:
        manifest = queue.pop()
        document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        for action in _action_uses(document):
            if not action.startswith("./"):
                continue
            candidate = root / action.removeprefix("./")
            choices = (
                (candidate / "action.yml", candidate / "action.yaml")
                if candidate.is_dir()
                else (candidate,)
            )
            local_manifest = next((path for path in choices if path.is_file()), None)
            if local_manifest is not None and local_manifest not in manifests:
                manifests.add(local_manifest)
                queue.append(local_manifest)
    return sorted(manifests)


def test_all_third_party_actions_are_pinned_to_full_commits():
    unpinned = _unpinned_actions(_action_manifests(ROOT))
    assert unpinned == []


def test_yaml_workflows_are_included_in_action_pin_checks(tmp_path):
    regular = tmp_path / "regular.yaml"
    regular.write_text("steps:\n  - uses : actions/checkout@v7\n", encoding="utf-8")
    flow = tmp_path / "flow.yaml"
    flow.write_text("steps: [{uses: actions/setup-python@v7}]\n", encoding="utf-8")

    assert _unpinned_actions([regular, flow]) == [
        "flow.yaml: actions/setup-python@v7",
        "regular.yaml: actions/checkout@v7",
    ]


def test_composite_actions_outside_github_are_included_in_pin_checks(tmp_path):
    action = tmp_path / "actions" / "example" / "action.yml"
    action.parent.mkdir(parents=True)
    action.write_text(
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - uses: actions/checkout@v7\n",
        encoding="utf-8",
    )

    assert _unpinned_actions(_action_manifests(tmp_path)) == [
        "action.yml: actions/checkout@v7"
    ]


def test_referenced_local_actions_are_followed_through_excluded_trees(tmp_path):
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "jobs:\n  test:\n    steps:\n      - uses: ./vendor/example\n",
        encoding="utf-8",
    )
    action = tmp_path / "vendor" / "example" / "action.yml"
    action.parent.mkdir(parents=True)
    action.write_text(
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - uses: actions/checkout@v7\n",
        encoding="utf-8",
    )

    assert _unpinned_actions(_action_manifests(tmp_path)) == [
        "action.yml: actions/checkout@v7"
    ]


def test_container_base_is_digest_pinned():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(
        r"^FROM python:3\.14-(?:alpine|slim)@sha256:[0-9a-f]{64}$",
        dockerfile,
        re.MULTILINE,
    )


def test_container_runtime_dependency_resolution_is_exactly_pinned():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    pins = set(re.findall(r'"([a-z0-9-]+==[^"]+)"', dockerfile))

    assert pins == {
        "anyio==4.14.2",
        "certifi==2026.7.22",
        "h11==0.16.0",
        "httpcore==1.0.9",
        "httpx==0.28.1",
        "idna==3.19",
    }


def test_container_recovery_reproducibility_limit_is_documented():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "RELEASE_CHECKLIST.md").read_text(encoding="utf-8")

    if "apk upgrade --no-cache" in dockerfile:
        for required in (
            "not guaranteed to be bit-for-bit identical",
            "immutable version-tag preflight must refuse a differing existing digest",
            "stop rather than overwriting that tag",
        ):
            assert required in checklist


def test_pull_requests_have_source_dependency_workflow_and_container_gates():
    codeql = (WORKFLOWS / "codeql.yml").read_text(encoding="utf-8")
    security = (WORKFLOWS / "security.yml").read_text(encoding="utf-8")

    assert "pull_request:" in codeql
    assert "security-events: write" in codeql
    assert "github/codeql-action/init@" in codeql
    assert "github/codeql-action/analyze@" in codeql

    assert "pip-audit==2.10.1" in security
    assert "zizmor==1.29.0" in security
    assert "--no-ignores" in security
    assert "--no-config" in security
    assert "bandit\n          --recursive src" in security
    assert "--skip" not in security
    assert "--ignore-nosec" in security
    assert "check-suppressions" in security
    assert "--min-severity=high" in security
    assert "aquasecurity/trivy-action@" in security
    assert "severity: HIGH,CRITICAL" in security
    assert "exit-code: \"1\"" in security


def test_release_artifacts_and_images_have_sboms_and_provenance():
    packages = (WORKFLOWS / "release-evidence.yml").read_text(encoding="utf-8")
    images = (WORKFLOWS / "docker.yml").read_text(encoding="utf-8")
    package_jobs = yaml.safe_load(packages)["jobs"]
    image_jobs = yaml.safe_load(images)["jobs"]

    assert "workflow_dispatch:" not in packages
    assert "if: github.ref_type == 'tag'" not in packages
    assert 'tags: ["v*"]' in packages
    assert "workflow_call:" in images
    assert 'tags: ["v*"]' not in images

    for workflow in (packages, images):
        assert "git merge-base --is-ancestor" in workflow
        assert "git cat-file -t" in workflow
        assert '")" = tag' in workflow
        assert 'test "$(git rev-parse origin/main)" = "$GITHUB_SHA"' not in workflow
        for release_gate in (
            "ruff check .",
            "scripts/validate_catalog.py",
            "scripts/check-counts",
            "scripts/check_release_ready.py --skip-build",
            "pytest --cov=freellmpool --cov-branch",
            "scripts/check_coverage.py .coverage.json",
        ):
            assert release_gate in workflow
        assert "code-scanning/alerts?state=open&severity=high" in workflow
        assert "code-scanning/alerts?state=open&severity=critical" in workflow

    assert "anchore/sbom-action@" in packages
    assert "pip-audit==2.10.1" in packages
    assert "scripts/security_exceptions.py validate" in packages
    assert package_jobs["gate"]["permissions"] == {
        "contents": "read",
        "security-events": "read",
    }
    assert package_jobs["packages"]["needs"] == "gate"
    assert package_jobs["packages"]["permissions"]["id-token"] == "write"
    assert packages.count("actions/attest@") >= 3
    assert "actions/upload-artifact@" in packages
    assert "dist/*.whl" in packages
    assert "dist/*.tar.gz" in packages
    assert package_jobs["container"]["needs"] == "packages"
    assert package_jobs["container"]["uses"] == "./.github/workflows/docker.yml"
    assert package_jobs["container"]["permissions"] == {
        "attestations": "write",
        "contents": "read",
        "id-token": "write",
        "packages": "write",
        "security-events": "read",
    }

    assert "anchore/sbom-action@" in images
    assert "aquasecurity/trivy-action@" in images
    assert images.index("aquasecurity/trivy-action@") < images.index(
        "Push immutable staging image"
    )
    assert images.count("docker/build-push-action@") == 1
    assert images.count("docker/setup-buildx-action@") == 2
    assert images.count("version: v0.36.1") == 2
    assert images.count(
        "image=moby/buildkit@sha256:"
        "28a898719c18a33f4e8000685287fa36fd0dd9560c6440227d3a732d79bb41d8"
    ) == 2
    assert "type=docker,dest=/tmp/freellmpool-image.tar" in images
    assert "input: /tmp/freellmpool-image.tar" in images
    assert "actions/download-artifact@" in images
    assert "sha256sum --check freellmpool-image.tar.sha256" in images
    assert "docker load --input" in images
    assert "docker run --rm --entrypoint python" in images
    assert 'docker run --rm "$image" --version' in images
    assert "/healthz" in images
    assert image_jobs["gate"]["permissions"] == {
        "contents": "read",
        "security-events": "read",
    }
    assert image_jobs["publish"]["needs"] == "gate"
    assert image_jobs["publish"]["permissions"]["packages"] == "write"
    assert images.count("actions/attest@") >= 2
    assert "push-to-registry: true" in images
    assert "staging-$GITHUB_SHA" in images
    assert images.index("Push immutable staging image") < images.index(
        "Generate downloadable image SBOM"
    )
    assert images.index("Generate downloadable image SBOM") < images.index(
        "Attest image provenance"
    )
    assert images.index("Attest image SBOM") < images.index(
        "Promote attested release image"
    )
    assert "docker buildx imagetools create --tag" in images
    assert "--prefer-index=false" in images
    assert "type=raw,value=latest" not in images
    assert "*:latest" not in images
    assert "Preflight immutable version tags" in images
    assert "manifest unknown|not found" in images
    assert "Refusing to overwrite immutable version tag" in images
    assert "existing_digest" in images
    assert images.index("Preflight immutable version tags") < images.index(
        "Promote attested release image"
    )


def test_latest_container_promotion_follows_authoritative_immutable_release():
    workflow = (WORKFLOWS / "promote-latest.yml").read_text(encoding="utf-8")
    jobs = yaml.safe_load(workflow)["jobs"]
    promote = jobs["promote"]

    assert "release:\n    types: [published]" in workflow
    assert "push:" not in workflow
    assert "pull_request:" not in workflow
    assert "group: ghcr-latest-promotion" in workflow
    assert "cancel-in-progress: false" in workflow
    assert promote["permissions"] == {
        "attestations": "read",
        "contents": "read",
        "packages": "write",
    }
    assert promote["if"] == (
        "github.event.release.draft == false && "
        "github.event.release.prerelease == false"
    )
    for required in (
        "gh_2.98.0_linux_amd64.tar.gz",
        "3b8ac6b30336802fc1a858d7c084e11cdf24ac1a761ca90b68022d7d729208de",
        "repos/$GITHUB_REPOSITORY/releases/latest",
        "repos/$GITHUB_REPOSITORY/releases/tags/$RELEASE_TAG",
        ".immutable",
        "git merge-base --is-ancestor",
        "git cat-file -t",
        "version: v0.36.1",
        "image=moby/buildkit@sha256:28a898719c18a33f4e8000685287fa36fd0dd9560c6440227d3a732d79bb41d8",
        "attestation verify",
        '"oci://$image@$version_digest"',
        '--source-ref "refs/tags/$RELEASE_TAG"',
        '--source-digest "$RELEASE_COMMIT"',
        '"$GITHUB_REPOSITORY/.github/workflows/docker.yml"',
        "--deny-self-hosted-runners",
        'imagetools create --tag "$latest_image"',
        'test "$latest_digest" = "$version_digest"',
    ):
        assert required in workflow
    assert workflow.count("repos/$GITHUB_REPOSITORY/releases/latest") >= 3
    assert workflow.index("attestation verify") < workflow.index(
        'imagetools create --tag "$latest_image"'
    )


def test_release_checklist_only_passes_distributions_to_twine():
    checklist = (ROOT / "docs" / "RELEASE_CHECKLIST.md").read_text(encoding="utf-8")

    assert "v0.11.4" not in checklist
    assert ".[dev,security]" in checklist
    assert 'twine check "$release_evidence_dir"/*.whl' in checklist
    assert '"$release_evidence_dir"/*.tar.gz' in checklist
    assert 'twine upload --skip-existing "$release_evidence_dir"/*.whl' in checklist
    assert "twine upload --skip-existing" in checklist
    subset = (
        'freellmpool "$release_version" "$release_evidence_dir" --mode subset'
    )
    exact = 'freellmpool "$release_version" "$release_evidence_dir" --mode exact'
    upload = 'twine upload --skip-existing "$release_evidence_dir"/*.whl'
    assert subset in checklist
    assert exact in checklist
    assert "published_artifacts_verified=false" in checklist
    assert 'test "$published_artifacts_verified" = true' in checklist
    assert checklist.index(subset) < checklist.index(upload) < checklist.index(exact)
    assert (
        "aquasec/trivy:0.72.0@sha256:"
        "cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f"
    ) in checklist
    assert "/var/run/docker.sock" not in checklist
    assert "--input /scan/freellmpool-image.tar" in checklist


def test_release_checklist_binds_provenance_and_publishes_immutable_assets():
    checklist = (ROOT / "docs" / "RELEASE_CHECKLIST.md").read_text(encoding="utf-8")

    for required in (
        "gh_2.98.0_linux_amd64.tar.gz",
        "3b8ac6b30336802fc1a858d7c084e11cdf24ac1a761ca90b68022d7d729208de",
        "repos/0xzr/freellmpool/immutable-releases",
        '--source-ref "refs/tags/$release_tag"',
        '--source-digest "$release_commit"',
        "--signer-workflow",
        "0xzr/freellmpool/.github/workflows/release-evidence.yml",
        "0xzr/freellmpool/.github/workflows/docker.yml",
        "--deny-self-hosted-runners",
        'release create "$release_tag"',
        "--draft",
        "--verify-tag",
        'release edit "$release_tag" --draft=false --latest',
        'release verify "$release_tag"',
        'release verify-asset "$release_tag" "$asset"',
        "--json isDraft,isImmutable",
        "repos/0xzr/freellmpool/releases/latest",
        'git show "${release_commit}:CHANGELOG.md"',
        "git show HEAD:pyproject.toml",
        'git ls-remote origin "refs/tags/$release_tag^{}"',
        'git cat-file -t "refs/tags/$release_tag"',
        "docker buildx version",
        'release download "$release_tag" --dir "$draft_assets_dir"',
        'sha256sum "$draft_assets_dir/$asset_name"',
        "run list --workflow promote-latest.yml",
        'run watch "$promotion_run_id" --exit-status',
        "run list --workflow pages.yml",
        'run watch "$pages_run_id" --exit-status',
        '"oci://ghcr.io/0xzr/freellmpool:latest"',
    ):
        assert required in checklist

    assert "isLatest" not in checklist
    assert checklist.count("```bash\n") == checklist.count(
        "```bash\nset -euo pipefail\n"
    )
    assert checklist.index("set -euo pipefail") < checklist.index(
        "gh_2.98.0_linux_amd64.tar.gz"
    )

    assert checklist.index('release create "$release_tag"') < checklist.index(
        'release edit "$release_tag" --draft=false --latest'
    )
    assert checklist.index('release edit "$release_tag" --draft=false --latest') < (
        checklist.index('release verify "$release_tag"')
    )
    clean_gate = 'test -z "$(git status --porcelain=v1 --untracked-files=all)"'
    assert checklist.count(clean_gate) >= 3
    remote_tag_gate = 'git ls-remote origin "refs/tags/$release_tag^{}"'
    assert checklist.count(remote_tag_gate) >= 3
    assert checklist.index(remote_tag_gate) < checklist.index(
        'release create "$release_tag"'
    )
    assert checklist.rindex(remote_tag_gate, 0, checklist.index("twine upload")) > (
        checklist.index("--mode subset")
    )
    asset_check = 'sha256sum "$draft_assets_dir/$asset_name"'
    assert checklist.index(asset_check) < checklist.index(
        'release edit "$release_tag" --draft=false --latest'
    )
    assert checklist.index('release edit "$release_tag" --draft=false --latest') < (
        checklist.index('run list --workflow promote-latest.yml')
    )
    assert checklist.index('run watch "$promotion_run_id" --exit-status') < (
        checklist.index('run watch "$pages_run_id" --exit-status')
    )
    pages_selector = checklist.split('pages_run_id=""', maxsplit=1)[1].split(
        "release_verified=false", maxsplit=1
    )[0]
    for required in (
        "run list --workflow pages.yml",
        '.headSha == \\"$release_commit\\"',
        '.headBranch == \\"$release_tag\\"',
        '.event == \\"release\\"',
        "sort_by(.databaseId) | last | .databaseId // empty",
        'test -n "$pages_run_id"',
        'run watch "$pages_run_id" --exit-status',
    ):
        assert required in pages_selector


def test_ci_and_release_evidence_validate_built_distribution_metadata():
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    release = (WORKFLOWS / "release-evidence.yml").read_text(encoding="utf-8")
    readiness = (ROOT / "scripts" / "check_release_ready.py").read_text(encoding="utf-8")

    assert "python -m twine check dist/*.whl dist/*.tar.gz" in ci
    assert "twine==7.0.0" in release
    assert "python -m twine check dist/*.whl dist/*.tar.gz" in release
    assert '"pip==26.2.1"' in readiness
    assert '"build==1.5.0"' in readiness
    assert '"twine==7.0.0"' in readiness
    assert '"pkginfo==1.12.1.2"' in readiness
    assert '"build>=1.2"' not in readiness


def test_security_enforcement_is_documented():
    enforcement = (ROOT / "docs" / "SECURITY_ENFORCEMENT.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "High-severity Python source audit",
        "Python dependency audit",
        "GitHub Actions audit",
        "Container vulnerability audit",
        "Analyze Python",
        "high_or_higher",
    ):
        assert required in enforcement


def test_security_tooling_extra_is_complete_and_pinned():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]

    assert "pyyaml>=6.0,<7" in extras["dev"]
    assert extras["security"] == [
        "bandit==1.9.4",
        "pip-audit==2.10.1",
        "zizmor==1.29.0",
    ]


def test_native_source_and_workflow_suppressions_are_rejected(tmp_path):
    source = tmp_path / "scripts" / "unsafe.py"
    source.parent.mkdir()
    source.write_text("dangerous()  # nosec B608\n", encoding="utf-8")
    workflow = tmp_path / ".github" / "actions" / "nested" / "action.yaml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "# zizmor: ignore[dangerous-triggers]\n"
        "steps:\n"
        "  - run: thing  # codeql[py/command-line-injection]\n",
        encoding="utf-8",
    )

    assert find_native_suppressions(tmp_path) == (
        ".github/actions/nested/action.yaml:1: zizmor suppression",
        ".github/actions/nested/action.yaml:3: CodeQL suppression",
        "scripts/unsafe.py:1: Bandit suppression",
    )


def test_dependabot_and_zizmor_config_suppressions_are_rejected(tmp_path):
    dependabot = tmp_path / ".github" / "dependabot.yml"
    dependabot.parent.mkdir()
    dependabot.write_text(
        "# zizmor: ignore[dependabot-execution]\nversion: 2\n",
        encoding="utf-8",
    )
    zizmor_config = tmp_path / "zizmor.yml"
    zizmor_config.write_text(
        "# zizmor: ignore[dangerous-triggers]\nrules: {}\n",
        encoding="utf-8",
    )

    assert find_native_suppressions(tmp_path) == (
        ".github/dependabot.yml:1: zizmor suppression",
        "zizmor.yml:1: zizmor suppression",
    )


def test_default_security_exception_registry_is_valid_and_empty():
    exceptions = load_exceptions(
        ROOT / ".github" / "security-exceptions.json",
        today=date(2026, 7, 29),
    )
    assert exceptions == ()
    assert ids_for(exceptions, "pip-audit") == ()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"expires": "2026-07-28"}, "expired"),
        ({"justification": "too short"}, "justification"),
        ({"owner": ""}, "owner"),
        ({"scanner": "unknown"}, "scanner"),
        ({"id": "../../escape"}, "id"),
        ({"issue": "http://example.test/1"}, "issue"),
    ],
)
def test_invalid_or_expired_security_exceptions_fail_closed(
    tmp_path,
    change,
    message,
):
    entry = {
        "id": "GHSA-abcd-1234-efgh",
        "scanner": "pip-audit",
        "justification": "False positive confirmed against the shipped call path.",
        "owner": "@maintainer",
        "expires": "2026-08-15",
        "issue": "https://github.com/0xzr/freellmpool/issues/999",
    }
    entry.update(change)
    path = tmp_path / "exceptions.json"
    path.write_text(
        json.dumps({"schema_version": 1, "exceptions": [entry]}),
        encoding="utf-8",
    )

    with pytest.raises(ExceptionPolicyError, match=message):
        load_exceptions(path, today=date(2026, 7, 29))


def test_private_advisory_exception_is_valid(tmp_path):
    path = tmp_path / "exceptions.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "exceptions": [
                    {
                        "id": "GHSA-abcd-1234-efgh",
                        "scanner": "pip-audit",
                        "justification": (
                            "False positive confirmed against the shipped call path."
                        ),
                        "owner": "@maintainer",
                        "expires": "2026-08-15",
                        "issue": (
                            "https://github.com/0xzr/freellmpool/"
                            "security/advisories/GHSA-abcd-1234-efgh"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exceptions = load_exceptions(path, today=date(2026, 7, 29))

    assert ids_for(exceptions, "pip-audit") == ("GHSA-abcd-1234-efgh",)
