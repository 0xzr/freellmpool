# Adoption Sprint Plan

Status: implementation complete; merge and npm distribution rollout pending
Owner: freellmpool maintainers
Planned execution: 2026-07-19
Branch: `codex/adoption-sprint-20260719`

## Objective and completion boundary

Reduce the time between discovering freellmpool and successfully using it from
an agent or an automated deployment. This implementation sprint delivers:

1. accurate positioning and a copy-pastable first run;
2. a first-class Hermes Agent profile;
3. tested, registry-ready OpenCode packages with corrected defaults; and
4. stable machine-readable liveness, readiness, and provider inventory APIs.

There are two intentionally separate completion states:

- **Implementation complete:** the reviewed PR is merged, post-merge CI passes,
  package tarballs pass clean-install/load smokes, and docs describe only
  installation paths that are actually available.
- **Distribution rollout complete:** both npm packages are published from the
  exact merge commit, verified from the registry, and docs are updated to make
  registry installation primary. If npm ownership credentials are unavailable,
  implementation may complete but distribution rollout is explicitly
  **blocked**, not reported as complete. Local-file instructions stay primary
  and registry commands stay labelled pending until verification.

## Evidence-to-work traceability

| Evidence (snapshot 2026-07-19) | Observed adoption gap | Work unit |
| --- | --- | --- |
| FreeLLMAPI's current README no longer matches this repository's comparison claims | Prospective users receive stale capability data | WU4 |
| OmniRoute exposes a broad CLI/MCP/agent surface | The comparison omits a close alternative and obscures freellmpool's narrower niche | WU4 |
| Hermes documents OpenAI-compatible custom endpoints | freellmpool has no tested Hermes setup despite requiring no adapter | WU2 |
| OpenCode packages exist only as repository directories; both npm names return `E404` | Installation requires repository paths and current defaults point to port 8765 instead of 8080 | WU3 |
| The proxy has `/healthz`, `/status`, and `/v1/models` but no readiness or allow-listed provider API | Operators and integrations must scrape UI-oriented state | WU1 |

Primary references:

- <https://github.com/tashfeenahmed/freellmapi>
- <https://github.com/diegosouzapw/OmniRoute>
- <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/providers.md>
- <https://opencode.ai/docs/plugins/>
- <https://docs.npmjs.com/trusted-publishers/>

## Declared file scope

Changes are restricted to this allow-list:

- Plan/release notes: `docs/ADOPTION_SPRINT_PLAN.md`, `CHANGELOG.md`.
- Operational APIs: `src/freellmpool/proxy.py`,
  `src/freellmpool/readiness.py`, `tests/test_proxy.py`,
  `tests/test_readiness.py`.
- Hermes: `src/freellmpool/profiles.py`, `tests/test_profiles.py`,
  `tests/test_agents.py`, `docs/INTEGRATIONS.md`, `README.md`.
- OpenCode: `integrations/opencode/{package.json,README.md,freellmpool.js,LICENSE}`,
  `integrations/opencode-tui/{package.json,README.md,index.tsx,LICENSE}`,
  `scripts/check_opencode_packages.mjs`, `tests/test_opencode_packages.py`,
  `.github/workflows/ci.yml`, `.github/workflows/publish-opencode.yml`,
  `tests/test_ci_config.py`, `docs/run-opencode-on-free-models.html`.
- Positioning/first run: `README.md`, `tests/test_release_metadata.py`.

Explicitly out of scope: provider/catalog additions, router or failover behavior,
dashboard/playground redesign, new protocol adapters, Python package publication,
image/vision/fusion features, broad static-site/SEO rewrites, and unrelated
cleanup. An all-catalog inventory is also out of scope; provider API totals mean
only the `Pool` instance serving the request.

## WU1: operational discovery APIs

Add these standard-library proxy routes:

- `/livez`: public liveness; `/healthz` remains a byte-for-byte JSON alias;
- `/readyz`: public advisory local-capacity readiness;
- `/v1/providers`: protected by the existing `/v1/models` auth policy; and
- `/v1/models?ready=true`: existing model shape filtered by local readiness.

No route performs an upstream request or changes logical quota counts, metrics,
or cooldown transitions.
The implementation takes one quota snapshot, one cooldown snapshot, and uses an
explicit output allow-list. Readiness is conservative and advisory: the router
may still try cooled or locally exhausted targets as a last resort, but the probe
reports them unavailable until their local condition clears.

Overlapping provider conditions use this stable precedence:
`unconfigured` > `no_enabled_models` > `cooldown` > `quota_exhausted` >
`ready`. Model status uses the same applicable order. Combination tests cover
each precedence boundary. Reading the quota snapshot may flush increments that
were already recorded by prior requests, but probes add no record, do not change
logical counts, and do not create a routing/cooldown transition.

### Stable schema v1

`GET /livez` and `GET /healthz` return HTTP 200 and preserve the legacy body:

```json
{"status": "ok"}
```

`GET /readyz` returns HTTP 200 when `ready_providers > 0`, otherwise HTTP 503:

```json
{
  "schema_version": 1,
  "status": "ready",
  "reason": "ready_providers_available",
  "ready_providers": 1,
  "total_providers": 2,
  "summary": {
    "ready": 1,
    "unconfigured": 0,
    "no_enabled_models": 0,
    "cooldown": 0,
    "quota_exhausted": 1
  }
}
```

When none are ready, `status` is `not_ready` and `reason` is
`no_ready_providers`. The stable provider/model status enum is `ready`,
`unconfigured`, `no_enabled_models`, `cooldown`, or `quota_exhausted`.

`GET /v1/providers` returns:

```json
{
  "schema_version": 1,
  "object": "list",
  "data": [{
    "id": "provider-id",
    "configured": true,
    "ready": true,
    "status": "ready",
    "enabled_models": 1,
    "ready_models": 1,
    "cooldown_remaining_s": 0.0,
    "models": [{
      "id": "provider-id/model-id",
      "name": "model-id",
      "ready": true,
      "status": "ready",
      "daily_limit": 1000,
      "used_today": 0,
      "remaining": 1000
    }]
  }]
}
```

Only those fields are emitted. `total_providers` and `data` mean the active
`Pool` inventory, including an unconfigured provider only when a caller
explicitly constructed such a Pool. `rpd == 0` means unknown/unmetered;
`daily_limit` and `remaining` are then `null`, and the model is not
considered exhausted. Missing credentials make `configured=false` and status
`unconfigured`. Disabled models are omitted.

For `/v1/models`, absent `ready` and exactly one `ready=false` or `ready=0`
preserve current behavior. Exactly one `ready=true` or `ready=1` filters the
list. Other values and repeated parameters return HTTP 400 with the existing
OpenAI error envelope. The filtered response preserves OpenAI-versus-Anthropic
content negotiation; routing aliases appear only when at least one backend model
is ready. Trailing slashes behave like existing routes.

Acceptance tests cover ready, unconfigured, no-enabled-model, exhausted,
unknown-quota, and cooldown states; both auth header styles; query parsing;
OpenAI/Anthropic shapes; empty filtered inventory; and assert that probes make no
upstream request and leave quota/cooldown state unchanged.

## WU2: Hermes Agent profile

Add `hermes` to the profile registry and legacy `freellmpool code` surface
using Hermes's documented custom-provider format:

```yaml
model:
  provider: custom
  default: quality
  base_url: http://localhost:8080/v1
  api_key: anything
```

The profile checks `hermes`, `freellmpool`, and `/v1/models`; installation
is print-only and points to `hermes model` as the interactive alternative.

Acceptance criteria:

- `profile list/show/install`, `profile doctor hermes --dry-run`, and
  `code hermes` expose the new profile;
- a non-dry-run doctor passes against a fake authenticated proxy;
- `--base-url` preserves Hermes's `/v1` custom endpoint contract; and
- registry/list/integration-guide parity tests cover Hermes automatically.

No Hermes files are edited and no claim is made that freellmpool is a native
Hermes provider.

## WU3: OpenCode distribution and default repair

Harden `opencode-freellmpool` and `opencode-freellmpool-tui` as independent
npm packages. Both use the actual proxy default `http://localhost:8080`; stale
`8765` and `freellmpool-proxy` references are removed. Manifests receive
public package metadata, root exports, `repository.directory`, issue/homepage
links, runtime/peer contracts, explicit file lists, and packaged licenses.
Lifecycle scripts are forbidden.

### Package validation contract

A dedicated Node 24 CI job installs npm 11.5.1 or newer and, for each package:

1. runs `npm pack --json` and asserts the exact tarball file set;
2. creates a clean temporary project and installs the tarball with scripts
   disabled;
3. resolves the declared root/subpath exports;
4. imports and invokes the server plugin far enough to validate its OpenCode
   contract with a stub API; and
5. uses Bun/OpenCode's runtime path to compile/load the TUI `./tui` export with
   its declared peers.

The same script is runnable locally. Python policy tests validate manifests, the
8080 default, absence of lifecycle hooks, workflow triggers/permissions, and docs
parity without making normal pytest perform network installs.

Until both registry versions are verified, local-file installation stays the
primary documented path and npm examples are explicitly marked pending.

### Publishing contract

`.github/workflows/publish-opencode.yml` is `workflow_dispatch` only, uses a
GitHub-hosted runner, Node 24/npm >=11.5.1, `contents: read` and
`id-token: write`, and the protected `npm` GitHub environment. Its required
inputs are package, version, and immutable merge SHA. It fetches `origin/main`,
checks out that SHA, verifies it is the current `origin/main` tip, verifies the
selected manifest version equals the input version, rejects a version already in
the registry, runs the package smoke, publishes only the selected package with
provenance, and verifies `npm view` plus the registry tarball. Per-package
dispatch makes partial rollout idempotently recoverable and avoids the existing
`v*` Docker-release trigger.

Because trusted publishing cannot bootstrap absent package names, the same
protected hosted workflow supports a one-time bootstrap secret. Rollout is:

1. after post-merge `main` CI, an npm owner creates a short-lived write token
   authorized to create the two unscoped packages and stores it only as the
   protected `npm` environment's `NPM_TOKEN` secret;
2. an environment approver dispatches the hosted workflow for each `0.1.0`
   package and exact merge SHA; `npm publish --provenance` uses that token while
   GitHub's hosted OIDC context supplies provenance;
3. verify each registry tarball before attempting the other package;
4. configure each package's trusted publisher for the exact repository and
   `publish-opencode.yml` workflow, then remove/revoke the bootstrap token; and
5. verify a later release through the approved `npm` environment/OIDC workflow.

If package one succeeds and package two fails, do not republish package one;
preserve its verified state and resume package two. A bad immutable version is
deprecated, corrected with a patch release, and reflected honestly in docs.
After both initial packages and trusted-publisher settings are verified, open a
second, docs-only PR from the verified registry state. That PR changes npm
installation from pending to primary, passes review and the full required CI,
merges, and has green post-merge `main` CI. Distribution rollout is not complete
before that second merge.

## WU4: positioning and first-run documentation

Correct the competitor table from commit-pinned primary sources, add OmniRoute,
show a 2026-07-19 snapshot date, and describe scope choices rather than vague
superiority. Stale FreeLLMAPI provider/MCP/audio claims are removed. Positioning
stays focused on freellmpool's small Python install, keyless-capable first run,
legitimate pooled free tiers, and agent-friendly OpenAI/MCP surfaces.

Add a no-checkout `uvx freellmpool ...` command alongside the portable virtual
environment path. Validate a clean local artifact with `uvx --from .` and run
the existing isolated live quickstart smoke
`FREELLMPOOL_QUICKSTART_PACKAGE=. scripts/quickstart-test.sh`; provider outage
is reported separately from packaging/CLI failure. New Hermes/API behavior is
identified as unreleased until the Python release containing it exists.

Update the README, integration guide, both package READMEs, and static OpenCode
guide in parity. Limitations remain prominent: upstream data handling, free-tier
volatility, local-readiness versus live-health semantics, and no rate-limit
bypass.

## Test-first execution and deterministic gates

1. Add and run failing proxy contract tests, then implement WU1.
2. Add and run failing Hermes tests, then implement WU2.
3. Add and run failing package/workflow tests, then implement WU3.
4. Add and run failing documentation assertions, then implement WU4.
5. Run targeted tests after each unit, then the full matrix.

The coverage gate uses the exact `.coverage-thresholds.json` command:

```bash
pytest --cov=freellmpool --cov-branch --cov-report=term-missing --cov-report=json:.coverage.json && python scripts/check_coverage.py .coverage.json
```

Additional deterministic gates:

```bash
ruff check .
mypy --follow-imports=skip src/freellmpool/routing_modes.py src/freellmpool/catalog_validation.py src/freellmpool/_version.py src/freellmpool/readiness.py
node scripts/check_opencode_packages.mjs
python scripts/check-counts
git diff --check
```

The complete `pytest` suite, build/release metadata checks, and existing Docker
smoke remain required through CI. New typed operational logic lives in the
strictly checked `readiness.py`; `proxy.py` and `profiles.py` have existing
full-module strict-mypy debt, so this sprint must not add errors outside that
typed helper. The explicit `--follow-imports=skip` still applies strict mode to
every named helper while isolating the gate from that known recursive debt.

## Security, review, merge, and rollout gates

- Health/provider payloads use explicit field construction; process environment,
  key names, values, and raw provider objects are never serialized.
- Public endpoints expose aggregate liveness/readiness only; named inventory is
  protected like `/v1/models`.
- Package installs disable scripts during validation, manifests cannot declare
  lifecycle hooks, publishing is never triggered by a PR, and future releases
  use environment-approved OIDC with least privilege.
- Existing `/healthz`, unfiltered `/v1/models`, and local OpenCode install
  paths remain backward compatible. No data migration is introduced.

Before implementation, independent feasibility, completeness, and
scope/alignment reviewers must all pass this plan. Before PR creation, final
adversarial review runs on the latest diff. Every blocking plan, code, security,
or PR-review finding must be fixed and retested before merge; only explicitly
non-blocking debt may be deferred.

PR completion requires all review comments resolved, all required and npm
validation checks green, merge, and post-merge `main` CI green. Distribution
then starts from the exact merge commit under the publishing contract above.
The final report states implementation and rollout status separately and never
describes an unpublished package as installable from npm.
