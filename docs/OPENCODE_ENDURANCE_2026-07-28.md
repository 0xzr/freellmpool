# OpenCode endurance run — 2026-07-28

This report records the sustained OpenCode validation used for PR #84. It is
an engineering log, not a benchmark: the run mixed investigation, regression
testing, candidate validation, and supervised rotations when an agent became
repetitive or proposed a change that conflicted with documented behavior.

## Run boundary

- Repository worktree: `/home/ubuntu/freellmpool-opencode-endurance`
- Candidate at final validation: `dc287653b835f8ed460b8844224087149bd3401c`
- Fixed start: `2026-07-28T11:39:58Z`
- Fixed six-hour-plus endpoint: `2026-07-28T17:40:58Z`
- Supervisor end: `2026-07-28T17:41:13Z`
- Fixed wall interval: 21,660 seconds (6 hours, 1 minute)
- Transcript: `/tmp/freellmpool-opencode-endurance-20260728.jsonl`
- Transcript SHA-256:
  `d70cc6d21717ec15ac20f502cdbab0291bc7edcb69eb4bd4db303c3e514ca611`

The supervisor kept work active across short, explicit agent rotations. It
used a separate worktree and proxy on port 8081, leaving the development proxy
on port 8080 untouched. Rotations did not reset the fixed start or endpoint.
The final candidate remained clean except for the worktree-local
`opencode.json`; candidate patches from earlier cycles were retained as stashes
until their reviewed equivalents were committed in the main worktree.
The final child was interrupted 15 seconds after the fixed endpoint so the
supervisor could exit; its `rc=130` is the expected interrupt status, not a
validation failure.

## Cycle record

| Cycles | Focus and result |
| --- | --- |
| 1–3 | Exercised quota, async, and CLI job paths. Found and validated job lifecycle hardening. |
| 4–7 | Tested context handling, installation boundaries, and the broader Python suite; 767 tests passed at that stage. |
| 8–10 | Found media endpoint status propagation gaps, refreshed to the reviewed fix, and revalidated the proxy baseline. |
| 11–13 | Exercised Responses API loops and a 20-minute non-proxy validation; no additional concrete defect survived reproduction. |
| 14–17 | Audited MCP, OpenCode packages, shell boundaries, and catalog behavior; focused suites passed. |
| 18–19 | Exercised agent workflow and reporting/observability paths. Candidate hypotheses were rejected when tests or call contracts disproved them. |
| 20 | Reproduced malformed inventory expiry data masking an earlier valid expiry. The reviewed implementation now parses dates, compares chronologically, and emits canonical ISO dates. |
| 21–22 | Reproduced unsafe custom-provider environment names producing invalid TOML and later data loss. The reviewed implementation rejects non-portable `key_env` and `extra_env` values. |
| 23–24 | Refreshed to the reviewed candidate and validated tailnet parsing and security paths; 49 focused tests passed. |
| 25–28 | Audited context/errors/routing modes, tokenmax, profiles, and CLI behavior. Several speculative edge cases were rejected because public callers enforce the relevant contracts. |
| 29 | Audited release tooling. A proposed default failure behavior was rejected because `--fail` intentionally makes that policy opt-in. |
| 30–33 | Repeated exact-head validation: full Python suite, branch coverage gate, release/catalog tests, OpenCode package tests, Ruff, and clean-tree checks. Cycle 33 was intentionally interrupted after the fixed endpoint. |

The supervisor stopped or rotated validation-only cycles when they became
repetitive. It also rejected speculative changes that lacked a failing
regression or contradicted an explicit interface. This kept the endurance run
useful without treating model output as authority.

## Findings carried into the candidate

The run directly exercised and validated these PR changes:

- resilient sustained-agent routing and quota backoff;
- correct async job completion and expiration behavior;
- preservation of client errors across streaming and media shims;
- chronological inventory expiry selection despite malformed entries; and
- portable custom-provider environment variable validation.

Every source change was independently reviewed by Codex Sol 5.6 at xhigh
reasoning before commit. One review blocked the first inventory implementation
because accepted ISO basic/week forms were still sorted lexically; the corrected
implementation parses `date` objects before selecting the minimum.

## Final validation

Cycle 30 validated exact head `dc28765` with:

- 807 Python tests passing;
- 85.67% line and 73.55% branch coverage, above the 80%/70% gates;
- 17 focused configuration tests passing;
- focused release metadata/tooling, catalog/vetting, and OpenCode package tests
  passing; and
- Ruff passing on the changed Python files.

GitHub Actions also passed `quickstart`, `docker-smoke`, `opencode-packages`,
and the Python 3.11, 3.12, 3.13, and 3.14 matrix at that exact head.

Cycle 31 independently completed two more full pytest passes, coverage,
release-tool tests, OpenCode package tests, deterministic catalog validation,
and Ruff. The first live provider-vetting attempt reached its network timeout;
a later retry completed successfully and wrote `/tmp/flp_vet_report.json`.
Those network-dependent results were retained as external observations rather
than treated as deterministic local failures. The cycle also corrected an
initial `python -m ruff` invocation after confirming that Ruff was installed as
a standalone executable.

## Final evidence

- Transcript size: 2,814 JSONL events / 8,786,250 bytes
- Parse result: 2,814 valid events, zero invalid lines
- OpenCode sessions: 32
- Tool uses: 906
  - 382 shell calls
  - 388 reads
  - 54 searches
  - 42 globs
  - 23 edits
  - 17 task-list updates
- Dedicated proxy totals:
  - 715 requests
  - 26,449,763 prompt tokens
  - 137,895 completion tokens
  - $407.08857 estimated API cost saved

The final worktree was still at `dc28765` with no tracked changes.
`opencode.json` was the only untracked runtime file. Four candidate/finding
stashes were retained for auditability. After telemetry collection, the
dedicated proxy was stopped and port 8081 was confirmed closed.
