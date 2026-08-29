# Release Checklist

Prepared release: next unreleased version. The package version must be bumped
before using this checklist; never reuse an existing tag.

This checklist is for the operator after the release PR is merged. Never publish,
push tags, or create a GitHub release from a feature or automation branch.
Run every shell block below in the same Bash session and in order. Each block
re-enables `set -euo pipefail`; missing state or any failed integrity check must
stop publication immediately.

## Verify

```bash
set -euo pipefail
python3 -m pip install -e ".[dev,security]"
ruff check .
python3 -m mypy --follow-imports=skip src/freellmpool/routing_modes.py \
  src/freellmpool/catalog_validation.py src/freellmpool/_version.py \
  src/freellmpool/readiness.py
PYTHONPATH=src python3 -m pytest
PYTHONPATH=src python3 -m pytest --cov=freellmpool --cov-branch \
  --cov-report=term-missing --cov-report=json:.coverage.json
python3 scripts/check_coverage.py .coverage.json
scripts/check-counts
PYTHONPATH=src python3 scripts/validate_catalog.py
python3 scripts/check_docs.py docs
python3 scripts/check_assets.py
PYTHONPATH=src python3 scripts/check_release_ready.py --skip-build
PYTHONPATH=src python3 scripts/check_release_ready.py
python3 scripts/security_exceptions.py validate
python3 scripts/security_exceptions.py check-suppressions
bandit --recursive src --severity-level high --confidence-level high --ignore-nosec
mapfile -t ignored < <(python3 scripts/security_exceptions.py ids pip-audit)
audit_args=()
for advisory in "${ignored[@]}"; do audit_args+=(--ignore-vuln "$advisory"); done
python3 -m pip_audit . --strict --desc=on --aliases=on "${audit_args[@]}"
zizmor --strict-collection --no-ignores --no-config \
  --min-severity=high --min-confidence=high .
docker build --tag freellmpool:release-check .
test "$(docker buildx version | awk '{print $2}')" = v0.36.1
trivy_ignore=$(mktemp)
python3 scripts/security_exceptions.py ids trivy > "$trivy_ignore"
trivy_scan_dir=$(mktemp -d)
docker save --output "$trivy_scan_dir/freellmpool-image.tar" \
  freellmpool:release-check
docker run --rm \
  -v "$trivy_scan_dir:/scan:ro" \
  -v "$trivy_ignore:/trivyignore:ro" \
  aquasec/trivy:0.72.0@sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f \
  image --input /scan/freellmpool-image.tar \
  --exit-code 1 --severity HIGH,CRITICAL --pkg-types os,library \
  --scanners vuln --ignorefile /trivyignore
```

Regenerate the three release cards with `python3 scripts/render_assets.py` before
these checks whenever their SVG copy changes. Commit the regenerated PNGs, the
matching `docs/assets/` copies, and `assets/asset-manifest.json` together.

The digest-pinned base image fixes the starting filesystem, but the deliberate
`apk upgrade --no-cache` resolves the then-current Alpine repository state. A
later recovery build is therefore not guaranteed to be bit-for-bit identical.
The immutable version-tag preflight must refuse a differing existing digest;
retain the original release evidence and stop rather than overwriting that tag.

Also validate the exact `server.json` with the checksum-pinned
`mcp-publisher 1.7.9` command used by CI, and run the plugin contract/build smoke
from `.github/workflows/ci.yml`. Before tagging, require the release PR checks,
the default-branch CodeQL scan, and the GitHub Pages validation job to be green.
The version-bump push intentionally leaves the previously released site live;
the `release: published` event deploys this version only after its non-draft
GitHub release and exact PyPI version both exist. Confirm the `github-pages`
environment permits the `v*` tag policy used by that deployment.
Confirm that the `mcp-registry` and `pypi` GitHub environments already exist and
are restricted to `main`; GitHub otherwise auto-creates unprotected environments
on first use. Publishing the plugin through Actions additionally requires its
PyPI trusted-publisher identity (or the controlled `PYPI_API_TOKEN` fallback) to
be configured before dispatch.

`check_release_ready.py` now bootstraps its own compatible `twine`/`pkginfo`
environment before running `twine check`, so host packaging-tool drift does not
block the full release smoke.

Use the checksum-pinned GitHub CLI for all release and attestation operations.
The distro CLI may be too old to verify immutable releases:

```bash
set -euo pipefail
release_cli_dir=$(mktemp -d)
gh_archive="$release_cli_dir/gh_2.98.0_linux_amd64.tar.gz"
curl --fail --location --silent --show-error \
  --output "$gh_archive" \
  https://github.com/cli/cli/releases/download/v2.98.0/gh_2.98.0_linux_amd64.tar.gz
printf '%s  %s\n' \
  3b8ac6b30336802fc1a858d7c084e11cdf24ac1a761ca90b68022d7d729208de \
  "$gh_archive" | sha256sum --check -
tar --extract --gzip --file "$gh_archive" --directory "$release_cli_dir"
release_gh="$release_cli_dir/gh_2.98.0_linux_amd64/bin/gh"
"$release_gh" --version | grep --fixed-strings 'gh version 2.98.0'
"$release_gh" attestation verify --help >/dev/null
"$release_gh" release verify --help >/dev/null
"$release_gh" release verify-asset --help >/dev/null
```

## Tag

Run after the PR is merged and the verified commit is on `main`:

```bash
set -euo pipefail
git switch main
git pull --ff-only
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
test -z "$(git status --porcelain=v1 --untracked-files=all)"
release_version=$(git show HEAD:pyproject.toml | python3 -c \
  'import sys, tomllib; print(tomllib.load(sys.stdin.buffer)["project"]["version"])')
release_tag="v$release_version"
release_commit=$(git rev-parse HEAD)
test -z "$(git tag --list "$release_tag")"
if "$release_gh" release view "$release_tag" >/dev/null 2>&1; then
  echo "Refusing to reuse existing release $release_tag" >&2
  exit 1
fi
test "$("$release_gh" api \
  -H 'Accept: application/vnd.github+json' \
  -H 'X-GitHub-Api-Version: 2026-03-10' \
  repos/0xzr/freellmpool/immutable-releases --jq .enabled)" = true
python3 - "$release_version" <<'PY'
import sys
import urllib.error
import urllib.request

try:
    urllib.request.urlopen(
        f"https://pypi.org/pypi/freellmpool/{sys.argv[1]}/json", timeout=20
    )
except urllib.error.HTTPError as exc:
    if exc.code == 404:
        raise SystemExit(0)
    raise
raise SystemExit("refusing to reuse an existing PyPI version")
PY
git tag -a "$release_tag" -m "freellmpool $release_version"
test "$(git cat-file -t "refs/tags/$release_tag")" = tag
git push origin "$release_tag"
```

## Build And Publish

The pushed tag starts `Release evidence`. Its Python gate and package-evidence
job must succeed before it invokes the reusable Docker build/scan/publish
workflow, preventing a container from publishing after failed package evidence.
Wait for the entire run to pass. Download the matching evidence artifact and
verify that its wheel and source archive attestations resolve to the tagged
commit:

```bash
set -euo pipefail
release_evidence_dir=$(mktemp -d)
release_run_id=$("$release_gh" run list --workflow release-evidence.yml \
  --limit 30 --json databaseId,headSha,headBranch,conclusion,event \
  --jq "map(select(.headSha == \"$release_commit\" and \
    .headBranch == \"$release_tag\" and .event == \"push\" and \
    .conclusion == \"success\")) | .[0].databaseId // empty")
test -n "$release_run_id"
"$release_gh" run download "$release_run_id" \
  --name "python-release-evidence-$release_tag" \
  --dir "$release_evidence_dir"
mapfile -t wheels < <(find "$release_evidence_dir" -maxdepth 1 -type f -name '*.whl')
mapfile -t sdists < <(find "$release_evidence_dir" -maxdepth 1 -type f -name '*.tar.gz')
mapfile -t sboms < <(find "$release_evidence_dir" -maxdepth 1 -type f -name '*.spdx.json')
test "${#wheels[@]}" -eq 1
test "${#sdists[@]}" -eq 1
test "${#sboms[@]}" -eq 2
package_attestation_args=(
  -R 0xzr/freellmpool
  --source-ref "refs/tags/$release_tag"
  --source-digest "$release_commit"
  --signer-workflow \
    0xzr/freellmpool/.github/workflows/release-evidence.yml
  --deny-self-hosted-runners
)
for artifact in "${wheels[@]}" "${sdists[@]}"; do
  "$release_gh" attestation verify "$artifact" \
    "${package_attestation_args[@]}"
done
"$release_gh" attestation verify "${wheels[0]}" \
  "${package_attestation_args[@]}" \
  --predicate-type https://spdx.dev/Document
"$release_gh" attestation verify "${sdists[0]}" \
  "${package_attestation_args[@]}" \
  --predicate-type https://spdx.dev/Document
python3 -m twine check "$release_evidence_dir"/*.whl \
  "$release_evidence_dir"/*.tar.gz
(cd "$release_evidence_dir" && sha256sum -- *.whl *.tar.gz *.spdx.json > SHA256SUMS)
```

Verify the immutable versioned container tag against the exact tag, commit, and
reusable signer workflow. The mutable `latest` tag is deliberately not assigned
until this release is published and authoritative:

```bash
set -euo pipefail
printf '%s' "$("$release_gh" auth token)" | \
  docker login ghcr.io --username 0xzr --password-stdin
container_attestation_args=(
  -R 0xzr/freellmpool
  --source-ref "refs/tags/$release_tag"
  --source-digest "$release_commit"
  --signer-workflow 0xzr/freellmpool/.github/workflows/docker.yml
  --deny-self-hosted-runners
)
"$release_gh" attestation verify \
  "oci://ghcr.io/0xzr/freellmpool:$release_version" \
  "${container_attestation_args[@]}"
```

Prepare the changelog excerpt and create a draft containing every final asset.
Release immutability takes effect at publication, so nothing may be attached
after the draft is published:

```bash
set -euo pipefail
git fetch --force --no-tags origin main:refs/remotes/origin/main
test "$(git rev-parse HEAD)" = "$release_commit"
git merge-base --is-ancestor "$release_commit" origin/main
test -z "$(git status --porcelain=v1 --untracked-files=all)"
mapfile -t remote_tag_rows < <(git ls-remote origin "refs/tags/$release_tag^{}")
test "${#remote_tag_rows[@]}" -eq 1
test "${remote_tag_rows[0]%%[[:space:]]*}" = "$release_commit"
release_notes_file=$(mktemp)
tagged_changelog=$(mktemp)
git show "${release_commit}:CHANGELOG.md" > "$tagged_changelog"
awk -v header="## [$release_version]" '
  index($0, header) == 1 { capture = 1; next }
  capture && /^## \[/ { exit }
  capture { print }
' "$tagged_changelog" > "$release_notes_file"
test -s "$release_notes_file"
release_assets=(
  "${wheels[@]}"
  "${sdists[@]}"
  "${sboms[@]}"
  "$release_evidence_dir/SHA256SUMS"
)
test "${#release_assets[@]}" -eq 5
"$release_gh" release create "$release_tag" \
  "${release_assets[@]}" \
  --draft --verify-tag \
  --title "freellmpool $release_version" \
  --notes-file "$release_notes_file"
test "$("$release_gh" release view "$release_tag" --json isDraft \
  --jq .isDraft)" = true
test "$("$release_gh" release view "$release_tag" --json assets \
  --jq '.assets | length')" -eq "${#release_assets[@]}"
```

Only the operator should upload those exact attested package files. The subset
check accepts an unpublished version or a matching partial upload, but rejects
any filename or SHA-256 conflict before `--skip-existing` can conceal it. The
exact check then requires PyPI to expose precisely the attested wheel and sdist;
rerunning this block safely resumes a matching partial upload:

```bash
set -euo pipefail
python3 scripts/verify_pypi_artifacts.py \
  freellmpool "$release_version" "$release_evidence_dir" --mode subset
mapfile -t remote_tag_rows < <(git ls-remote origin "refs/tags/$release_tag^{}")
test "${#remote_tag_rows[@]}" -eq 1
test "${remote_tag_rows[0]%%[[:space:]]*}" = "$release_commit"
python3 -m twine upload --skip-existing "$release_evidence_dir"/*.whl \
  "$release_evidence_dir"/*.tar.gz
published_artifacts_verified=false
for attempt in $(seq 1 12); do
  if python3 scripts/verify_pypi_artifacts.py \
    freellmpool "$release_version" "$release_evidence_dir" --mode exact; then
    published_artifacts_verified=true
    break
  fi
  if test "$attempt" -lt 12; then sleep 5; fi
done
test "$published_artifacts_verified" = true
```

After upload, smoke-test the published artifact:

```bash
set -euo pipefail
python3 scripts/check_release_ready.py --check-pypi
```

## Post-Release

Publish the already complete draft. This freezes the tag and all five assets and
causes GitHub to issue the release attestation. Never include API keys, provider
credentials, or unpublished issue draft details in the release notes.

```bash
set -euo pipefail
git fetch --force --no-tags origin main:refs/remotes/origin/main
test "$(git rev-parse HEAD)" = "$release_commit"
git merge-base --is-ancestor "$release_commit" origin/main
test -z "$(git status --porcelain=v1 --untracked-files=all)"
mapfile -t remote_tag_rows < <(git ls-remote origin "refs/tags/$release_tag^{}")
test "${#remote_tag_rows[@]}" -eq 1
test "${remote_tag_rows[0]%%[[:space:]]*}" = "$release_commit"
draft_state=$("$release_gh" release view "$release_tag" --json isDraft --jq .isDraft)
if test "$draft_state" = true; then
  test "$("$release_gh" release view "$release_tag" --json name --jq .name)" = \
    "freellmpool $release_version"
  test "$("$release_gh" release view "$release_tag" --json body --jq .body)" = \
    "$(cat "$release_notes_file")"
  test "$("$release_gh" release view "$release_tag" --json tagName --jq .tagName)" = \
    "$release_tag"
  test "$("$release_gh" release view "$release_tag" --json isImmutable \
    --jq .isImmutable)" = false
  draft_assets_dir=$(mktemp -d)
  "$release_gh" release download "$release_tag" --dir "$draft_assets_dir"
  mapfile -t downloaded_assets < <(find "$draft_assets_dir" -maxdepth 1 \
    -type f -printf '%f\n' | sort)
  mapfile -t expected_assets < <(printf '%s\n' "${release_assets[@]}" | \
    while IFS= read -r asset; do basename "$asset"; done | sort)
  test "${#downloaded_assets[@]}" -eq 5
  test "${downloaded_assets[*]}" = "${expected_assets[*]}"
  for asset in "${release_assets[@]}"; do
    asset_name=$(basename "$asset")
    test "$(sha256sum "$draft_assets_dir/$asset_name" | cut -d ' ' -f 1)" = \
      "$(sha256sum "$asset" | cut -d ' ' -f 1)"
  done
  "$release_gh" release edit "$release_tag" --draft=false --latest
else
  test "$draft_state" = false
fi
promotion_run_id=""
for attempt in $(seq 1 12); do
  promotion_run_id=$("$release_gh" run list --workflow promote-latest.yml \
    --limit 30 --json databaseId,headSha,headBranch,event \
    --jq "map(select(.headSha == \"$release_commit\" and \
      .headBranch == \"$release_tag\" and .event == \"release\")) | \
      sort_by(.databaseId) | last | .databaseId // empty")
  if test -n "$promotion_run_id"; then break; fi
  if test "$attempt" -lt 12; then sleep 5; fi
done
test -n "$promotion_run_id"
"$release_gh" run watch "$promotion_run_id" --exit-status
pages_run_id=""
for attempt in $(seq 1 24); do
  pages_run_id=$("$release_gh" run list --workflow pages.yml \
    --limit 30 --json databaseId,headSha,headBranch,event \
    --jq "map(select(.headSha == \"$release_commit\" and \
      .headBranch == \"$release_tag\" and .event == \"release\")) | \
      sort_by(.databaseId) | last | .databaseId // empty")
  if test -n "$pages_run_id"; then break; fi
  if test "$attempt" -lt 24; then sleep 5; fi
done
test -n "$pages_run_id"
"$release_gh" run watch "$pages_run_id" --exit-status
release_verified=false
for attempt in $(seq 1 12); do
  if "$release_gh" release verify "$release_tag"; then
    release_verified=true
    break
  fi
  if test "$attempt" -lt 12; then sleep 5; fi
done
test "$release_verified" = true
for asset in "${release_assets[@]}"; do
  "$release_gh" release verify-asset "$release_tag" "$asset"
done
test "$("$release_gh" release view "$release_tag" \
  --json isDraft,isImmutable \
  --jq '[.isDraft, .isImmutable] | @tsv')" = $'false\ttrue'
test "$("$release_gh" api repos/0xzr/freellmpool/releases/latest \
  --jq .tag_name)" = "$release_tag"
"$release_gh" attestation verify \
  "oci://ghcr.io/0xzr/freellmpool:latest" \
  "${container_attestation_args[@]}"
version_manifest=$(mktemp)
latest_manifest=$(mktemp)
docker buildx imagetools inspect \
  "ghcr.io/0xzr/freellmpool:$release_version" --raw > "$version_manifest"
docker buildx imagetools inspect \
  ghcr.io/0xzr/freellmpool:latest --raw > "$latest_manifest"
test "$(sha256sum "$version_manifest" | cut -d ' ' -f 1)" = \
  "$(sha256sum "$latest_manifest" | cut -d ' ' -f 1)"
```

After the root package is visible on PyPI, dispatch `publish-mcp.yml` with the
exact release version and immutable release source SHA (`$release_commit`); its post-publish step must prove
normalized equality with the official registry. If the bundled
`llm-freellmpool` version changed, dispatch `publish-llm-plugin.yml` for that
same immutable release source SHA only after its required root dependency is on PyPI, then require its digest
and fresh-install verification jobs to pass.

Finally, verify the immutable tag and GitHub release, PyPI wheel/sdist digests,
MCP registry record, plugin package (when published), versioned and `latest`
GHCR images, provenance attestations, and the deployed Pages URLs. Close or
supersede maintenance issues/PRs only after their default-branch evidence is
green.
