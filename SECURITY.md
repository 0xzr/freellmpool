# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities through
[GitHub private vulnerability reporting](https://github.com/0xzr/freellmpool/security/advisories/new).
Do not include credentials, provider keys, prompts, or other sensitive data in a
public issue. Include the affected version or commit, a minimal reproduction,
impact, and any suggested mitigation. Maintainers will acknowledge a complete
report as soon as practical and coordinate disclosure after a fix is available.

The latest release and current `main` receive security fixes. Older versions may
require upgrading to the latest fixed release.

## Automated gates

Pull requests and `main` are checked by:

- CodeQL `security-extended` queries for Python, with code-scanning annotations;
- Bandit, failing on high-confidence/high-severity Python findings;
- `pip-audit`, failing on any known vulnerable resolved Python dependency;
- zizmor, failing on high-confidence/high-severity GitHub Actions findings;
- Trivy, failing on high or critical OS/library findings in the built image; and
- a repository test requiring every third-party Action to use a full commit SHA
  and the Python base image to use an OCI digest.

Release tag workflows build wheel/source artifacts and the GHCR image, generate
SPDX JSON SBOMs, and create GitHub artifact attestations. The image additionally
publishes OCI SBOM/provenance attestations.

Scanner and Action versions are pinned in the workflows. Dependabot proposes
updates for Python, Actions, and Docker references.
The required GitHub status contexts and CodeQL ruleset are recorded in
[`docs/SECURITY_ENFORCEMENT.md`](docs/SECURITY_ENFORCEMENT.md).

## Reproducing checks locally

Install the pinned security extra, then run the same policy and scanner
arguments used by CI:

```bash
python3 -m pip install -e ".[dev,security]"
python3 scripts/security_exceptions.py validate
python3 scripts/security_exceptions.py check-suppressions
bandit --recursive src --severity-level high --confidence-level high --ignore-nosec
mapfile -t ignored < <(python3 scripts/security_exceptions.py ids pip-audit)
audit_args=()
for advisory in "${ignored[@]}"; do audit_args+=(--ignore-vuln "$advisory"); done
python3 -m pip_audit . --strict --desc=on --aliases=on "${audit_args[@]}"
zizmor --strict-collection --no-ignores --no-config \
  --min-severity=high --min-confidence=high .
pytest -q tests/test_supply_chain.py
```

Reproduce the container gate with Trivy v0.72.0:

```bash
docker build --tag freellmpool:security .
trivy_ignore=$(mktemp)
python3 scripts/security_exceptions.py ids trivy > "$trivy_ignore"
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$trivy_ignore:/trivyignore:ro" \
  aquasec/trivy:0.72.0 image \
  --exit-code 1 --severity HIGH,CRITICAL --pkg-types os,library --scanners vuln \
  --ignorefile /trivyignore \
  freellmpool:security
```

CodeQL uses GitHub's hosted database builder; inspect its workflow annotations
and uploaded SARIF in the Actions run.

## False positives and temporary exceptions

Fix or upgrade first. The repository exception registry supports only
`pip-audit` and Trivy because those scanners can exclude an exact advisory
without hiding unrelated findings. If one of those findings is demonstrably
inapplicable and cannot be removed immediately:

1. Open a public issue with the scanner ID, affected artifact/path, reachability
   analysis, compensating controls, and removal plan. Use a private advisory if
   the analysis itself is sensitive.
2. Add one entry to `.github/security-exceptions.json` with the scanner, exact
   finding ID, accountable GitHub owner, issue URL, justification, and expiry.
3. Keep expiry within 90 days. Expired, malformed, duplicate, or overlong
   exceptions fail closed in CI.
4. Obtain the normal xhigh security review. Remove the exception as soon as the
   underlying finding is fixed.

Bandit `# nosec`, CodeQL/LGTM inline directives, and zizmor inline ignores are
forbidden and checked in CI. Bandit also runs with `--ignore-nosec`, so an
obfuscated or missed comment cannot weaken its gate. CodeQL false positives
must be dismissed in GitHub's code-scanning alert UI with a linked issue and
review rationale; the active code-scanning ruleset remains the merge
enforcement point.

Never suppress a class of findings globally, lower the configured severity
threshold, use `continue-on-error`, or add `|| true` to a security gate.

## Verifying release provenance

Download the matching workflow artifact, then verify package or image provenance:

```bash
gh attestation verify dist/freellmpool-*.whl -R 0xzr/freellmpool
gh attestation verify dist/freellmpool-*.tar.gz -R 0xzr/freellmpool
gh attestation verify oci://ghcr.io/0xzr/freellmpool:VERSION -R 0xzr/freellmpool
```

SPDX attestations can be verified by adding:

```bash
--predicate-type https://spdx.dev/Document/v2.3
```
