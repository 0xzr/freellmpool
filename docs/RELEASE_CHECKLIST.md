# Release Checklist

Prepared release: next unreleased version. The package version must be bumped
before using this checklist; never reuse an existing tag.

This checklist is for the operator after the polish PR is merged. Do not publish,
push tags, or create a GitHub release from the polish automation branch.

## Verify

```bash
python -m pip install -e ".[dev,security]"
ruff check .
PYTHONPATH=src python3 -m pytest
scripts/check-counts
PYTHONPATH=src python3 scripts/validate_catalog.py
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
trivy_ignore=$(mktemp)
python3 scripts/security_exceptions.py ids trivy > "$trivy_ignore"
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$trivy_ignore:/trivyignore:ro" aquasec/trivy:0.72.0 image \
  --exit-code 1 --severity HIGH,CRITICAL --pkg-types os,library \
  --scanners vuln --ignorefile /trivyignore freellmpool:release-check
```

`check_release_ready.py` now bootstraps its own compatible `twine`/`pkginfo`
environment before running `twine check`, so host packaging-tool drift does not
block the full release smoke.

## Tag

Run after the PR is merged and the verified commit is on `main`:

```bash
git switch main
git pull --ff-only
release_version=$(python3 -c \
  'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
release_tag="v$release_version"
test -z "$(git tag --list "$release_tag")"
if gh release view "$release_tag" >/dev/null 2>&1; then
  echo "Refusing to reuse existing release $release_tag" >&2
  exit 1
fi
git tag -a "$release_tag" -m "freellmpool $release_version"
git push origin "$release_tag"
```

## Build And Publish

The pushed tag starts the `Release evidence` and `Docker` workflows. Each
workflow revalidates the tag and security gates before publishing anything.
Wait for both to pass. Download the matching evidence artifact and verify that
its wheel and source archive attestations resolve to the tagged commit:

```bash
release_evidence_dir=$(mktemp -d)
gh run download --name "python-release-evidence-$release_tag" \
  --dir "$release_evidence_dir"
gh attestation verify "$release_evidence_dir"/*.whl -R 0xzr/freellmpool
gh attestation verify "$release_evidence_dir"/*.tar.gz -R 0xzr/freellmpool
python3 -m twine check "$release_evidence_dir"/*.whl \
  "$release_evidence_dir"/*.tar.gz
```

Only the operator should upload those exact attested package files:

```bash
python3 -m twine upload "$release_evidence_dir"/*.whl \
  "$release_evidence_dir"/*.tar.gz
```

After upload, smoke-test the published artifact:

```bash
python3 scripts/check_release_ready.py --check-pypi
```

## Post-Release

Create the GitHub release from `$release_tag` and paste its changelog entry.
Attach the downloaded SPDX JSON files. Verify the GHCR image provenance with
`gh attestation verify
oci://ghcr.io/0xzr/freellmpool:$release_version -R 0xzr/freellmpool`. Do not
include API keys, provider credentials, or unpublished issue draft details in
the release notes.
