# Product UX + Tailnet Plan

Status: Kimi/M3 co-planned; final metaswarm plan review passed

## Goal

Make freellmpool feel like a useful local AI appliance, not just a provider pool.
The first successful experience should be:

1. `pip install freellmpool`
2. `freellmpool init`
3. choose where to use it: local CLI, OpenCode, Metaswarm, Cline, Cursor, Codex, Python, or Tailnet
4. get a working command/config/report within a few minutes

The plan keeps the project honest: no rate-limit bypass, no account automation,
no silent paid routing, no hidden remote telemetry, and no heavyweight UI stack.

## Product Shape

### New user-facing commands

```bash
freellmpool init
freellmpool init --agent metaswarm --tailnet

freellmpool profile list
freellmpool profile show metaswarm
freellmpool profile install opencode
freellmpool profile doctor metaswarm

freellmpool ask --role coder "fix this failing test"
freellmpool ask --role critic < patch.diff
freellmpool ask --second-opinion "is this design solid?"
freellmpool roles

freellmpool battle "which answer is strongest?"
freellmpool playground

freellmpool recipe list
freellmpool recipe run pr-review --input patch.diff
freellmpool recipe run repo-summary --path .

freellmpool jobs add --recipe repo-summary --path docs/
freellmpool jobs run
freellmpool jobs watch

freellmpool report last --markdown
freellmpool report last --html --open

FREELLMPOOL_MODE=wise freellmpool ask --role cheap "summarize this"
freellmpool quota-wise status

freellmpool tailnet status
freellmpool tailnet serve --port 8080
freellmpool tailnet connect <tailnet-host-or-ip>
```

### First-class experiences

- **Setup wizard:** one guided path that detects providers, keys, existing agents,
  Tailscale, and the current proxy state.
- **Agent profiles:** explicit profiles for `metaswarm`, `opencode`, `codex`,
  `cline`, `cursor`, `claude`, `aider`, and `continue`.
- **Roles instead of model IDs:** `coder`, `critic`, `summarizer`,
  `long-context`, `cheap`, `fast`, and `second-opinion`.
- **Battle/playground:** side-by-side answers from multiple free models with a
  small local HTML UI and CLI output.
- **Second-opinion everywhere:** one shared panel primitive exposed through CLI,
  recipes, MCP, reports, and the playground.
- **Recipes:** reusable workflows for practical jobs like PR review, repo
  summaries, launch-copy critique, and model second opinions.
- **Job queue:** local overnight/batch work that embraces free-tier slowness
  instead of pretending every request is instant.
- **Shareable reports:** Markdown/HTML artifacts for battles, jobs, and recipe
  runs.
- **Tailnet gateway:** a secure LAN/Tailscale mode for using one freellmpool
  proxy from multiple personal machines and agents.
- **Quota-wise mode:** user intent for conserving scarce free quota, lowering
  default token use, and warning before expensive multi-model calls.

## Constraints

- Keep implementation stdlib-first. Existing dependency style is intentionally
  light; do not add React, Flask, FastAPI, SQLite dependencies, or rich TUI
  dependencies for the first slices.
- Network-provider tests remain fake/offline by default.
- Tailnet support shells out to the local `tailscale` CLI when available. It
  must degrade gracefully when Tailscale is not installed, not logged in, or has
  no IPv4 address.
- Tailnet serving requires proxy auth by default. An interactive live run may
  generate a temporary random bearer token only when stderr is a TTY. A
  noninteractive live run must receive `--api-key` or
  `FREELLMPOOL_PROXY_KEY`; otherwise it exits 2 before server startup. Never
  expose configured provider API keys.
- Quota-wise mode uses local counters and user-declared quota metadata. It must
  not auto-poll provider accounts, create accounts, rotate identities, or infer
  ways around provider limits.
- `set_policy`-style remote mutation is out of scope. MCP additions in this
  plan are read-only or explicit run commands.

## Shared Primitives

Several features should share one implementation contract instead of each
inventing its own format:

- `profiles.py`: profile records with provider/model, role hints, model family,
  and `cost_class` in `free | metered | paid`. `cost_class` is an advisory and
  enforcement label; it never authorizes silent paid routing.
- `roles.py`: role resolution that maps user intent to existing pool arguments
  and profile preferences.
- `panel.py`: bounded multi-model fan-out used by battle, second-opinion,
  recipes, reports, and MCP.
- `artifacts.py`: `RunRecord` data shared by battles, recipes, jobs, reports,
  and later cost/quota audit output. The core artifact type lands with reports
  before any command depends on it.
- `mode.py`: mode policy for `normal` and `wise`, backed by local quota data and
  user-declared quota hints.

## Top 10 Feature Mapping

Kimi and M3 both pushed this plan toward explicit product features instead of
generic infrastructure. The top 10 feature ideas map to work units as follows:

| # | Feature idea | Plan coverage |
|---|---|---|
| 1 | Init wizard | WU-002 |
| 2 | Agent profiles, including Metaswarm | WU-003 |
| 3 | Roles instead of model IDs | WU-004 |
| 4 | Battle/playground | WU-005 |
| 5 | Recipe library | WU-007 |
| 6 | Background job queue | WU-009 |
| 7 | Second-opinion everywhere | WU-006 |
| 8 | Tailnet/LAN gateway | WU-001 |
| 9 | Shareable reports | WU-008 |
| 10 | Use my free quota wisely mode | WU-010 |

WU-011 exposes the UX through MCP. WU-012 makes the public story, docs, and
release checks match the shipped commands.

## Kimi/M3 Top-10 Planning Addendum

Collaboration source: Kimi K2.7 Code and MiniMax M3 reviewed this plan on
2026-06-19. Kimi focused on practical UX gaps, copy-pastable flows, and
quantified acceptance criteria. M3 focused on codebase-feasible sequencing,
cross-work-unit integration, and testable edge cases.

Adopted resolutions:

- Keep the approved work-unit structure. The plan already maps the top 10
  feature ideas 1:1 to WU-001 through WU-010.
- Do not reopen closed work units only to rewrite history. Closed WUs are
  treated as shipped slices; the addendum guides dependent WUs, docs, and any
  follow-up hardening.
- Keep `second-opinion` as a role name only after WU-006 provides the panel
  helper. WU-004 may reserve the role, but WU-006 owns fan-out behavior.
- Keep `panel.py` focused on provider/model/latency/text/error records. Report
  and cost-class enrichment belongs in WU-008 `RunRecord` artifacts.
- Keep the stdlib-first constraint. None of these refinements authorizes a new
  runtime dependency.

Fresh Kimi/M3 reconciliation, 2026-06-19:

- Kimi K2.6 and MiniMax M3 both returned `NEEDS_REVISION` on the top-10 plan
  review because several surfaces were not yet pinned to versioned schemas,
  explicit override precedence, or concrete failure behavior. The revisions
  below are adopted as plan requirements, not optional polish.
- After these revisions, Kimi K2.6 and MiniMax M3 both returned `PASS` with no
  blocking missing requirements.
- Version every structured public surface that agents may consume:
  `init --json`, `recipe list --json`, bundled recipe JSON, job JSONL events,
  and WU-008 `RunRecord` artifacts. Consumers must ignore unknown future keys
  but tests must pin the current required keys.
- Closed-set fields are required for role/profile contracts: `client_kind`,
  `cost_class`, role names, routing modes, and report feature types. Unknown
  values should fail clearly at parse/validation boundaries.
- Override precedence is explicit across the plan: user-supplied `--model`,
  `--providers`, `--routing`, and `--max-tokens` win over role/profile/mode
  defaults, and human-facing output must show when such an override happened.
- Multi-model fan-out is centralized in `panel.py`. CLI, MCP, battle, recipes,
  reports, and later job flows must call the same helper or a thin wrapper
  around it, not parallel fan-out implementations.
- Wise-mode guards are policy, not routing magic. Expensive free-quota
  operations prompt in interactive mode, fail in non-interactive mode unless
  `--yes` is passed, and never fall through to paid providers without an
  explicit paid selection.
- Append-only local state uses event records, not in-place mutation. Reports
  and jobs must define replay/newest semantics from the JSONL stream rather
  than relying on fragile mutable pointer files.

### 1. Init Wizard

Use case: a new user runs `freellmpool init` once and receives a working path
for local CLI, OpenCode, Metaswarm, MCP, or Tailnet without reading every doc.

Adopted plan additions:

- `freellmpool init --yes` with no other flags is detect-only: print providers,
  agent CLIs, Tailscale state, proxy config state, and the recommended next
  command; write no files and exit 0.
- Add `--json` output for the detected environment so agents can consume init
  results without scraping prose.
- `init --json` uses schema version `1.0.0` with required keys:
  `detected_providers`, `detected_agents`, `tailscale_state`,
  `proxy_config_state`, `recommended_next_command`, `recommended_profile`, and
  `warnings`.
- Each offered path prints one copy-pastable command block. The Metaswarm +
  Tailnet path must include `tailnet serve`, remote base URLs, and the profile
  doctor command.
- Re-running init against existing config remains idempotent. No config file is
  changed without an explicit write path, and existing config can be overwritten
  only when `--force` is present.

Verification additions:

- Empty environment plus `init --yes` exits 0, says there is nothing configured
  yet, and writes nothing.
- `init --yes --agent metaswarm --tailnet` prints Tailnet and Metaswarm commands
  even when Tailscale is missing, with clear missing-step text.
- Existing config plus a second init run performs no writes unless `--force` is
  present.

### 2. Agent Profiles, Including Metaswarm

Use case: the user can run one command to see exactly how freellmpool should be
wired into Metaswarm, OpenCode, Codex, Cline, Cursor, Claude, Aider, or Continue.

Adopted plan additions:

- The `metaswarm` profile must expose one free/cheap worker lane, one larger
  reviewer lane, Codex as escalation, and Opus only as a final-review lane. Any
  paid lane is explicitly labeled `cost_class=paid`,
  `enabled_by_default=false`, and off by default.
- `profile show metaswarm` must include Tailnet-aware base URL examples for a
  second machine using the proxy over Tailscale.
- `profile install <name>` stays print-first for third-party agent config. If a
  write mode is added, it writes only freellmpool-owned config and lists every
  file changed.
- Profile records include `client_kind`, `base_url`, role map, model family,
  cost class, snippets, and doctor checks.
- `client_kind` is a closed set: `openai`, `anthropic`, `mcp`, and `shell`.
  Doctor checks classify each result as blocking or advisory.

Verification additions:

- `profile list` includes `metaswarm`.
- `profile show metaswarm` includes worker lane, reviewer lane, Codex
  escalation, Opus final-review-only wording, and Tailnet base URL snippets.
- Resolver tests prove a free profile wins over a paid profile when both satisfy
  the requested role, and explicit paid model overrides remain visible.

### 3. Roles Instead Of Model IDs

Use case: a user asks for intent, such as `coder`, `critic`, `cheap`, or
`fast`, without memorizing model IDs or provider quirks.

Adopted plan additions:

- Publish the shipped WU-004 role-default mapping in tests and docs:
  `coder=quality/2048`, `critic=quality/temperature 0.1`, `summarizer=spread/768`,
  `long-context=quality/4096`, `cheap=spread/512`, `fast=fast/512`,
  `conserve=spread/512`, and `second-opinion=reserved placeholder until WU-006
  activates panel behavior`.
- `--role` creates ordinary pool arguments; it does not create another routing
  engine.
- `--model`, `--providers`, `--routing`, and `--max-tokens` remain explicit
  overrides. Output provenance must show that the user overrode role defaults.
- `freellmpool roles` prints one role per line. Unknown-role errors list valid
  role names.

Verification additions:

- `freellmpool roles` is stable and easy to parse, one role per line.
- `ask --role coder --model <model>` calls the explicit model and displays the
  override.
- Unknown role errors list valid roles.

### 4. Battle CLI And Local Playground

Use case: users compare several free model answers side by side from the CLI or
a local page, then decide which answer is strongest.

Adopted plan additions:

- `battle` defaults to three models when available, runs with fewer when fewer
  providers are configured, and caps requested model count at five.
- `battle --synthesize` reuses the second-opinion synthesis path once WU-006
  exists.
- `playground --port <port>` prints the existing `/playground` URL if a proxy
  is already reachable; it must not start nested proxy servers.
- `/playground` is self-contained, framework-free HTML/JS with no external
  network resources, no CDN scripts, no external stylesheets, and no remote
  images.
- `/dashboard` and `/playground` share a public, data-free shell; protected
  usage, inventory, ready-model, and battle data is fetched only with a bearer
  header. After submission the input is cleared and the token stays only in a
  JavaScript closure until reload, never in a URL, cookie, Web Storage,
  rendered DOM, global, log, or HTML response.
- `POST /freellmpool/battle` enforces the same proxy auth as `/v1`.
- `POST /freellmpool/battle` accepts JSON fields `prompt`, `n` or `models`,
  `max_tokens`, `routing`, and `synthesize`; it returns `answers`,
  `synthesis`, `truncated`, and `markdown`.

Verification additions:

- Fewer than three fake providers warns and still renders available answers.
- Per-model failures render inline and do not fail the whole comparison.
- The served playground bytes contain no CDN, remote script, stylesheet, or
  image references.

### 5. Recipe Library

Use case: users run useful workflows immediately: second opinion, PR review,
repo summary, launch-copy critique, and Metaswarm worker review.

Adopted plan additions:

- Recipe JSON schema includes name, version, description, role, prompt template,
  variables, input mode, and output mode.
- CLI input modes map explicitly to prompt text, stdin, `--input <file>`, and
  `--path <glob>`.
- Recipe templates use standard-library `string.Template` syntax. Missing
  variables fail before provider calls and list the missing variable names.
- `recipe run second-opinion` calls the WU-006 panel helper; it must not
  duplicate fan-out.
- `metaswarm-worker-review` is concrete: input is a worker summary or patch plus
  validation output, role is `critic`, and output is blocking findings,
  verification gaps, and an approve/revise verdict.
- Every bundled recipe has a copy-pastable example command.
- `recipe list --json` uses schema version `1.0.0`, and bundled recipe files
  include a `version` field for forward-compatible changes.

Verification additions:

- `recipe list --json` has a stable versioned schema.
- Bad recipe name and missing required input produce distinct errors.
- Built wheel/sdist artifacts include every bundled recipe JSON file.

### 6. Background Job Queue

Use case: free-tier work can be slow, so users queue repo summaries, reviews, or
reports and let freellmpool process them locally.

Adopted plan additions:

- Job records are append-only JSONL with `id`, `created_at`, `spec`, `status`,
  attempt metadata, and optional tombstone/cancel records.
- Add `jobs cancel <id>`.
- `jobs watch` in the first foreground-only slice tails/render-refreshes the
  local JSONL state; no daemon is implied.
- Job JSONL records are events with schema version `1.0.0` and event types
  `queued`, `started`, `completed`, `failed`, and `cancelled`. Cancellation is a
  new event, not mutation of an earlier queued record.
- Duplicate recipe/path submissions create distinct jobs unless an explicit
  `--dedupe` option is added.
- `jobs run --max-failures N` halts after N consecutive failed job executions
  within that run, preserves each failure, and does not corrupt unrelated queued
  jobs.

Verification additions:

- Queue replay after restart preserves cancelled status.
- A failed job records its error and later jobs remain runnable.
- `jobs run --dry-run` shows execution order without mutating the queue.

### 7. Second-Opinion Everywhere

Use case: a user or agent can ask several free models for independent answers
from CLI, MCP, recipes, reports, and the playground.

Adopted plan additions:

- Panel defaults to three models, minimum two when available, and maximum five.
- Selection prefers distinct providers and model families, but a single-family
  configured environment still runs rather than failing.
- Synthesis uses a prompt template owned by `panel.py`; synthesizer choice may
  be one of the answer models, but synthesis failure is non-fatal.
- CLI `ask --second-opinion` and MCP panel tools import the same shared helper.
  Later WU-005, WU-007, and WU-008 consumers must use that helper rather than
  duplicating fan-out.
- Wise mode can warn or require confirmation before expensive fan-out; WU-010
  owns the policy, WU-006 exposes the hook.
- The shared helper returns structured records with provider, model, latency,
  text, and error. Synthesis failures are represented as non-fatal errors while
  preserving individual answers for CLI output and later report records.

Verification additions:

- Single-family fake provider setup still returns answers.
- MCP and CLI tests prove both call the shared panel helper.
- Synthesis failure leaves individual answers visible in CLI output and later
  report records.

### 8. Tailnet/LAN Gateway

Use case: one machine runs the freellmpool proxy and another personal machine or
agent uses it safely over Tailnet.

Adopted plan additions:

- `tailnet connect` is print-only by default. If probing is added, it is behind
  an explicit `--probe` flag.
- `tailnet status` gets the Tailnet IPv4 from `tailscale ip -4`; MagicDNS is a
  useful extra, not a requirement.
- Interactive generated session tokens are URL-safe, session-only, printed
  exactly once at serve startup, and never persisted to config, reports, or
  status output. Setup hints use `<session-token-shown-above>`.
- Noninteractive live runs fail before server startup unless `--api-key` or
  `FREELLMPOOL_PROXY_KEY` is present. Dry runs use only
  `<session-token-printed-on-real-run>` and never mint a usable secret.
- Unsafe non-loopback binds continue to require explicit LAN acknowledgement and
  auth. `--allow-lan` without auth is refused unless the user also passes the
  explicit `--allow-no-auth` escape hatch.

Verification additions:

- Missing Tailscale, logged-out Tailscale, malformed output, and MagicDNS-less
  setups produce actionable output.
- An interactive generated token appears once in serve output and not in
  `tailnet status`; setup hints contain only the placeholder.
- A noninteractive no-key run exits 2 before the proxy starts and names
  `--api-key`, `FREELLMPOOL_PROXY_KEY`, and the interactive option.
- Dry-run output contains only the safe token marker.
- Refused binds return non-zero with a clear auth/allow-lan message.

### 9. Shareable Reports

Use case: after a battle, recipe, job, or second-opinion run, the user gets a
Markdown or HTML artifact they can inspect, keep, or post.

Adopted plan additions:

- Run IDs and report paths are deterministic under the user data directory, for
  example `reports/<run-id>.md` and `reports/<run-id>.html`.
- `RunRecord` uses schema version `1.0.0`. `report last` reads the newest valid
  run record by append order, not by filesystem mtime or a mutable pointer.
- `report last --html --open` uses the OS opener when available and falls back
  to printing the path.
- HTML reports are self-contained and escaped. Markdown reports preserve prose,
  but HTML treats prompts, model text, errors, labels, and recipe names as
  untrusted input.
- `cost show <run-id>` is an explicit WU-008 report adjunct. It must fail
  clearly when the run is missing and suggest `report list`. Cost/quota output
  uses local catalog hints, local quota counters, and recorded metadata; it does
  not call provider billing APIs.

Verification additions:

- HTML tests cover tags, quotes, script-like text, and external URL rejection.
- Redaction covers obvious API-key and bearer-token shapes.
- `report list` and `report last` operate from append-only run records rather
  than a fragile mutable pointer.

### 10. Use My Free Quota Wisely Mode

Use case: users can say "save my scarce free quota" and see freellmpool lower
token use, spread traffic, and refuse expensive accidental fan-out.

Adopted plan additions:

- Wise mode output includes a visible mode line showing routing and max-token
  defaults unless quiet/JSON output is requested.
- `quota-wise status` prints per-provider local headroom and exactly one
  recommended mode/action.
- Partial exhaustion skips exhausted free providers when alternatives remain;
  total declared free-quota exhaustion returns a clear `QUOTA_EXHAUSTED` result
  and never falls through to paid providers.
- Explicit user flags such as `--routing quality`, `--model`, `--providers`,
  and `--max-tokens` override mode defaults and are shown as overrides.
- Expensive operations include `tokenmax`, panel/battle fan-out above the wise
  threshold, and large job batches. Interactive runs prompt; non-interactive
  runs fail fast unless `--yes` is present. `--yes` authorizes the requested
  free-quota operation; it does not authorize silent paid routing.

Verification additions:

- Wise mode banner appears for normal ask output and notes explicit overrides.
- Partial exhaustion and total exhaustion are separate tests.
- Expensive multi-model operations are guarded consistently across battle,
  second-opinion, recipes, jobs, and MCP.

## Work Units

### WU-001: Tailnet Gateway Mode

Purpose: make freellmpool easy to run on one machine and consume from another
machine over Tailnet.

Files:

- Existing: `src/freellmpool/cli.py`
- Existing: `src/freellmpool/proxy.py`
- New: `src/freellmpool/tailnet.py`
- Existing tests: `tests/test_cli.py`, `tests/test_proxy.py`
- New tests: `tests/test_tailnet.py`
- Docs: `README.md`, `docs/INTEGRATIONS.md`

Implementation:

- Add `tailnet` subcommands:
  - `tailnet status`: report Tailscale availability, local Tailnet IPv4, MagicDNS
    hostname if discoverable, and whether an auth key is configured.
  - `tailnet serve`: bind proxy to the Tailnet IPv4 by default, require auth,
    and print exact client setup commands for OpenAI-compatible clients.
  - `tailnet connect <host>`: print and optionally probe the remote
    `OPENAI_BASE_URL=http://<host>:<port>/v1` endpoint.
- Add `proxy --tailnet` as a convenience alias for `tailnet serve`.
- Use `subprocess.run(["tailscale", "ip", "-4"])` with timeout for IP discovery.
- Prefer binding to the discovered Tailnet IPv4 address. Treat Tailscale `100.x`
  addresses as the expected safe bind target.
- Generate a session-only token with `secrets.token_urlsafe()` only for a live
  interactive run where stderr is a TTY and no `--api-key` or
  `FREELLMPOOL_PROXY_KEY` is present. Noninteractive no-key runs exit 2 before
  calling the proxy server; dry runs print a fixed safe marker instead.
- Refuse `0.0.0.0` or non-Tailnet LAN binds unless the user passes explicit
  `--allow-lan` and auth is enabled. Refuse unauthenticated non-loopback serving
  unless the user passes an explicit `--allow-no-auth` escape hatch.

Definition of Done:

- `freellmpool tailnet status` works without Tailscale installed and explains
  what is missing.
- `freellmpool tailnet serve --dry-run` prints a concrete bind address, the
  fixed `<session-token-printed-on-real-run>` marker, dashboard URL, and client
  env vars without creating a secret or starting a server.
- Noninteractive live serving without `--api-key` or
  `FREELLMPOOL_PROXY_KEY` exits 2 before server startup; interactive generation
  prints the key exactly once and uses `<session-token-shown-above>` in hints.
- Unit tests monkeypatch `subprocess.run` for installed, missing, logged-out,
  and malformed Tailscale output.
- Unit tests cover unsafe bind refusal for `0.0.0.0` without `--allow-lan`.
- Existing proxy auth tests still pass.

Verification:

- `PYTHONPATH=src python3 -m pytest tests/test_tailnet.py tests/test_cli.py tests/test_proxy.py`

### WU-002: Init Wizard

Purpose: make the first run point users to a working path instead of a wall of
docs.

Files:

- Existing: `src/freellmpool/cli.py`
- Existing: `src/freellmpool/agents.py`
- New: `src/freellmpool/init_wizard.py`
- Existing tests: `tests/test_cli.py`, `tests/test_agents.py`
- New tests: `tests/test_init_wizard.py`
- Docs: `README.md`, `docs/INTEGRATIONS.md`

Implementation:

- Add `freellmpool init` with interactive and non-interactive modes.
- Detect:
  - configured providers from `configured_providers()`
  - keyless provider availability from catalog metadata
  - installed agent CLIs using `shutil.which`
  - Tailscale availability via `tailnet.py`
  - existing proxy config/key settings
- Offer paths:
  - local CLI only
  - OpenAI-compatible proxy
  - coding-agent profile
  - Tailnet gateway
  - MCP server
- Non-interactive examples:
  - `freellmpool init --agent opencode --yes`
  - `freellmpool init --agent metaswarm --tailnet --yes`
- The first slice prints commands and writes only explicitly confirmed config
  files. It does not auto-edit third-party agent config by default.
- Re-running `freellmpool init` is idempotent. It must not clobber user-edited
  profile, quota, or proxy config files unless `--force` is explicitly passed.

Definition of Done:

- `freellmpool init --yes --agent opencode` prints an actionable setup plan.
- `freellmpool init --yes --agent metaswarm --tailnet` includes Tailnet serve
  and remote client setup.
- Interactive prompts are testable by monkeypatching `input`.
- Re-running against existing config prints current status and exits cleanly
  without rewriting files unless `--force` is present.
- Missing providers and missing Tailscale produce clear next steps, not stack
  traces.

Verification:

- `PYTHONPATH=src python3 -m pytest tests/test_init_wizard.py tests/test_cli.py tests/test_agents.py`

### WU-003: Agent Profiles and Metaswarm Profile

Purpose: turn existing `freellmpool code <agent>` recipes into richer,
installable profiles with model roles and doctor checks.

Files:

- Existing: `src/freellmpool/agents.py`
- Existing: `src/freellmpool/cli.py`
- New: `src/freellmpool/profiles.py`
- Existing tests: `tests/test_agents.py`, `tests/test_cli.py`
- New tests: `tests/test_profiles.py`
- Docs: `docs/INTEGRATIONS.md`, `docs/AGENTS.md`, `README.md`

Implementation:

- Add `profile list`, `profile show <name>`, `profile doctor <name>`, and
  `profile install <name>`.
- Keep `freellmpool code <agent>` as a compatibility alias that renders the
  profile quick-start.
- Add a first-class `metaswarm` profile:
  - one free/cheap worker lane
  - one larger reviewer lane
  - optional Codex/Opus lanes documented as user-owned paid tools, not routed
    silently through freellmpool
  - Tailnet URL support for agents on another machine
- Add profile metadata fields:
  - `client_kind`: openai, anthropic, mcp, shell
  - `base_url`
  - recommended role map
  - model family
  - `cost_class`: free, metered, paid
  - config snippets
  - doctor checks
- Add a role/profile resolver contract: roles prefer profiles with compatible
  family/capability and the safest cost class; explicit `--model` remains an
  escape hatch and is always visible in output.
- `profile doctor` verifies binaries, env vars, proxy reachability, `/v1/models`,
  `/v1/responses` where relevant, and `/v1/messages` for Claude-compatible flows.

Definition of Done:

- Existing `code` tests still pass.
- `freellmpool profile show metaswarm` includes Tailnet-aware config.
- `profile show` surfaces each profile's cost class and model family.
- Resolver tests prove roles never silently upgrade from free to paid.
- `profile doctor metaswarm --dry-run` reports checks without network calls.
- `profile doctor opencode` can be tested against a fake local proxy.

Verification:

- `PYTHONPATH=src python3 -m pytest tests/test_profiles.py tests/test_agents.py tests/test_cli.py tests/test_proxy.py`

### WU-004: Role-Based Asking

Purpose: let users ask for useful work by role instead of provider/model IDs.

Files:

- Existing: `src/freellmpool/cli.py`
- Existing: `src/freellmpool/router.py`
- Existing: `src/freellmpool/capability.py`
- New: `src/freellmpool/roles.py`
- Existing tests: `tests/test_cli.py`, `tests/test_routing.py`,
  `tests/test_capability.py`
- New tests: `tests/test_roles.py`
- Docs: `README.md`, `docs/INTEGRATIONS.md`

Implementation:

- Add `freellmpool roles`.
- Add `freellmpool ask --role <role>`.
- Initial roles:
  - `coder`: quality routing, code-capable prompt hints
  - `critic`: quality routing, low temperature, higher max token default
  - `summarizer`: fast/spread routing
  - `long-context`: prefer larger context windows where catalog metadata exists
  - `cheap`: spread/conserve mode
  - `fast`: latency-aware routing
  - `second-opinion`: panel handoff suggestion
- Role resolution produces ordinary existing pool arguments: routing mode, model
  preference, max token defaults, and optional system prompt prefix.
- Add `freellmpool ask --routing <mode>` and an optional `routing` argument to
  `Pool.ask`, implemented by forwarding through the existing `Pool.chat`
  routing path. Until that lands, role-based asking must call `Pool.chat`
  directly when it needs a non-default routing mode.
- Role resolution can consult profile metadata when WU-003 is available, but it
  must preserve existing explicit `--model` and `--providers` behavior.
- Do not introduce a new routing engine in this work unit.

Definition of Done:

- `ask --role coder` passes expected routing/max-token/system hints to `Pool.ask`.
- `ask --routing quality` is tested and preserves current default behavior when
  the flag is omitted.
- `roles` output is short and actionable.
- Unknown role errors list valid roles.
- Existing `ask --model` and `--providers` behavior remains unchanged.
- If `--role` and `--model` are both supplied, output provenance must make the
  explicit model choice visible.

Verification:

- `PYTHONPATH=src python3 -m pytest tests/test_roles.py tests/test_cli.py tests/test_routing.py`

### WU-005: Battle CLI and Local Playground

Purpose: make model comparison tangible and demo-worthy.

Files:

- Existing: `src/freellmpool/cli.py`
- Existing: `src/freellmpool/proxy.py`
- Existing: `src/freellmpool/tokenmax.py`
- New: `src/freellmpool/battle.py`
- Existing tests: `tests/test_cli.py`, `tests/test_proxy.py`,
  `tests/test_tokenmax.py`
- New tests: `tests/test_battle.py`
- Docs: `README.md`

Implementation:

- Add `freellmpool battle <prompt>`:
  - defaults to 3-5 distinct providers
  - prints side-by-side Markdown sections
  - records labels, latency, failures, and selected synthesis if requested
  - can emit a report via WU-008 once available
- Add `freellmpool playground`:
  - starts the proxy if requested or prints the existing `/playground` URL
  - first slice can reuse the running proxy and serve a self-contained
    `/playground` page from `proxy.py`
- Add proxy endpoints:
  - `GET /playground`: public, data-free static HTML/JS shell shared with
    `/dashboard`
  - `POST /freellmpool/battle`: local JSON endpoint that runs a bounded panel
- Keep the page framework-free and visually focused on comparing answers.
- Keep browser authentication closure-only: clear the form input after
  submission, use only bearer headers for protected requests, and re-prompt
  after reload. Do not persist or render the token.

Definition of Done:

- `battle` works with fake providers in tests.
- `/playground` renders without external assets.
- Public shell bytes contain no usage, inventory, model, battle, or bearer data;
  protected browser requests remain header-authenticated.
- `/freellmpool/battle` enforces the same proxy auth as `/v1`.
- Failures are shown per model without failing the whole battle.

Verification:

- `PYTHONPATH=src python3 -m pytest tests/test_battle.py tests/test_proxy.py tests/test_cli.py`

### WU-006: Second-Opinion Everywhere

Purpose: make "ask a few free models and compare" a first-class user flow
instead of a hidden implementation detail.

Files:

- Existing: `src/freellmpool/cli.py`
- Existing: `src/freellmpool/tokenmax.py`
- Existing: `src/freellmpool/mcp_server.py`
- New: `src/freellmpool/panel.py`
- Existing from WU-004: `src/freellmpool/roles.py`
- Later consumer from WU-005: `src/freellmpool/battle.py`
- New tests: `tests/test_panel.py`
- Existing tests: `tests/test_cli.py`, `tests/test_mcp.py`
- Docs: `README.md`, `docs/MCP.md`

Implementation:

- Add a shared `panel.py` helper used by `battle`, `ask --second-opinion`,
  recipes, and MCP tools.
- Panel inputs:
  - messages or prompt
  - desired model count
  - routing mode
  - optional role
  - max tokens and timeout
  - synthesize yes/no
- Panel behavior:
  - pick distinct providers where possible
  - prefer distinct model families for second-opinion calls where family
    metadata exists
  - default to three models
  - preserve individual answers even if synthesis fails
  - return structured answer records with provider/model, latency, text, and error
- Add `freellmpool ask --second-opinion`.
- Add a `second-opinion` role that delegates to the panel helper.
- Keep existing MCP `free_llm_panel`, but update wording/defaults so the tool is
  clearly the agent-facing second-opinion surface. Add `free_llm_second_opinion`
  only if product clarity wins over keeping the tool list smaller.

Definition of Done:

- `ask --second-opinion` prints at least two model answers and an optional
  synthesis when fake providers are configured.
- Panel clamps model count and token limits.
- Synthesis failure is non-fatal and leaves the individual answers visible.
- Cross-family second-opinion tests prove the helper avoids same-family reruns
  when an alternative family is available, and gracefully skips validation when
  only one family exists.
- MCP panel tests cover the same shared helper.
- WU-006 exposes a stable helper contract for later battle reuse. WU-005 owns
  the battle integration tests.

Verification:

- `PYTHONPATH=src python3 -m pytest tests/test_panel.py tests/test_cli.py tests/test_mcp.py`

### WU-007: Recipes

Purpose: ship useful workflows users can run immediately.

Files:

- Existing: `src/freellmpool/cli.py`
- Existing: `pyproject.toml`
- New: `src/freellmpool/recipes.py`
- New package data: `src/freellmpool/recipes/*.json`
- New tests: `tests/test_recipes.py`
- Docs: `README.md`, `docs/INTEGRATIONS.md`

Implementation:

- Add recipe commands: `recipe list`, `recipe show`, `recipe run`.
- Use JSON recipe files to avoid a YAML dependency.
- Update package-data/build configuration so bundled recipe JSON files are
  included in wheels and source distributions.
- Recipe schema:
  - name
  - description
  - role
  - prompt template
  - input mode: prompt, stdin, file, path glob
  - output mode: text, markdown report
- Initial recipes:
  - `second-opinion`
  - `pr-review`
  - `repo-summary`
  - `launch-copy-critic`
  - `metaswarm-worker-review`
- Recipes call existing `Pool.ask`, `battle`, or `panel`-style helpers rather
  than creating a separate execution engine.

Definition of Done:

- `recipe list` shows bundled recipes.
- `recipe run second-opinion --input text.txt` works with fake providers.
- Bad recipe names and missing inputs produce clear errors.
- Recipe package data is included by the build config.
- A wheel built from the tree contains the bundled recipe JSON files.

Verification:

- `PYTHONPATH=src python3 -m pytest tests/test_recipes.py tests/test_cli.py`
- `PYTHONPATH=src python3 -m build` or existing release metadata check if package data changes.

### WU-008: Reports

Purpose: generate artifacts users can keep, send, and post.

Files:

- Existing: `src/freellmpool/cli.py`
- New: `src/freellmpool/reports.py`
- New: `src/freellmpool/artifacts.py`
- New tests: `tests/test_reports.py`
- Docs: `README.md`

Implementation:

- Add report commands:
  - `report last --markdown`
  - `report last --html`
  - `report list`
  - `report open <path>`
- Store run records as append-only JSONL under the user config dir, with
  override env vars for tests. `report last` reads the newest run record rather
  than relying on a single mutable pointer.
- Report types:
  - battle
  - recipe
  - job batch
- Add a shared `RunRecord` dataclass for battle, recipes, jobs, and
  second-opinion output.
- Add `freellmpool cost show <run-id>` once `RunRecord` exists; it prints role,
  profile, family, cost class, and local quota/headroom for that run.
- Markdown first, minimal HTML second.
- HTML reports are self-contained: no CDN references, external scripts,
  external stylesheets, or remote images. Use small embedded CSS/SVG only.
- Include provider/model provenance, latency, failures, timestamp, prompt title,
  and estimated cost avoided when available.
- Redact bearer tokens and configured API key-looking strings from report text.
- Escape all user/model-supplied text in HTML reports with the standard library
  before writing it into markup. Markdown reports may preserve prose, but HTML
  reports must treat prompts, model output, provider labels, errors, and recipe
  names as untrusted text.

Definition of Done:

- Battle/recipe helpers can write a report object.
- `report last` prints the expected file path/content.
- `report list` shows recent run IDs and types.
- Redaction tests cover obvious API key and bearer-token shapes.
- HTML report tests cover prompt/output strings containing tags, quotes, and
  script-like text, and verify they are escaped rather than executable.
- Self-contained report tests reject `http://`, `https://`, `//cdn`, external
  `src=`, or external stylesheet references.
- `cost show <run-id>` has a fake `RunRecord` test covering role, profile,
  model family, cost class, and local quota/headroom output.

Verification:

- `PYTHONPATH=src python3 -m pytest tests/test_reports.py tests/test_battle.py tests/test_recipes.py`

### WU-009: Local Job Queue

Purpose: make freellmpool useful for slow, quota-aware background work.

Files:

- Existing: `src/freellmpool/cli.py`
- New: `src/freellmpool/jobs.py`
- New tests: `tests/test_jobs.py`
- Docs: `README.md`

Implementation:

- Add `jobs add`, `jobs list`, `jobs run`, and `jobs watch`.
- Queue format is append-only JSONL under the user config dir.
- First slice runs jobs synchronously in the foreground; no daemon.
- Jobs can reference a recipe, role, prompt, file, or path glob.
- `jobs run` processes one job at a time, records status, and writes reports.
- Add `--limit`, `--max-failures`, and `--dry-run`.
- Persist job state as append-only JSONL with tombstone/cancel records so a
  crashed process can replay queue state on restart.

Definition of Done:

- Queue survives process restart.
- Cancelled jobs remain cancelled after queue replay.
- Failed jobs preserve error details and do not block unrelated queued jobs.
- `jobs run --dry-run` shows what would run.
- Reports are written for completed jobs.

Verification:

- `PYTHONPATH=src python3 -m pytest tests/test_jobs.py tests/test_reports.py tests/test_recipes.py`

### WU-010: Quota-Wise Mode

Purpose: let users express "save my best free quota" as a product mode, not as
a pile of manual routing flags.

Files:

- Existing: `src/freellmpool/cli.py`
- Existing: `src/freellmpool/quota.py`
- Existing: `src/freellmpool/routing_modes.py`
- Existing: `src/freellmpool/config.py`
- New from WU-004: `src/freellmpool/roles.py`
- New: `src/freellmpool/mode.py`
- New tests: `tests/test_mode.py`
- Existing tests: `tests/test_cli.py`, `tests/test_roles.py`, `tests/test_quota.py`
- Docs: `README.md`, `docs/INTEGRATIONS.md`

Implementation:

- Add mode resolution from:
  - per-command `--mode`
  - `FREELLMPOOL_MODE`
  - optional config setting
- Initial modes:
  - `normal`: current behavior
  - `wise`: prefer spread/fair routing, lower default max tokens for broad
    workflows, and warn before expensive multi-model calls
- Add `quota-wise status` to show local per-provider headroom using existing
  quota counters and catalog RPD hints.
- Add optional user quota declarations in config, for example a
  `~/.config/freellmpool/quotas.toml`-compatible shape handled by stdlib TOML
  readers where available. These declarations are local user hints, not
  provider account polling.
- Add `cheap` and `conserve` role behavior:
  - `cheap`: low token defaults and spread routing
  - `conserve`: skip or warn on providers with low local headroom
- In wise mode, require confirmation for expensive operations such as
  `tokenmax`, large `battle` panels, or large job batches unless `--yes` is set.
- When declared/local free quota is exhausted, halt with a clear
  `QUOTA_EXHAUSTED`-style error and suggest waiting for reset or making an
  explicit paid choice outside the default flow. Never auto-fall through to paid
  providers.
- Never route to paid providers automatically. Wise mode conserves free quota;
  it does not bypass limits or automate account rotation.

Definition of Done:

- `FREELLMPOOL_MODE=wise freellmpool ask ...` changes defaults in tests without
  breaking explicit `--model`, `--providers`, `--max-tokens`, or `--routing`.
- `quota-wise status` reports local headroom and gives one recommended mode.
- Exhausted declared quota returns a clear non-zero result in tests and does not
  call a paid provider.
- Wise mode warns/prompts before expensive multi-model calls in interactive
  mode and fails clearly rather than hanging in non-interactive mode.
- Report integration is optional until WU-008 exists; once it lands, reports can
  include quota mode and local headroom summary.

Verification:

- `PYTHONPATH=src python3 -m pytest tests/test_mode.py tests/test_cli.py tests/test_roles.py tests/test_quota.py`

### WU-011: MCP UX Tools

Purpose: expose the new workflows to Claude Code, Cursor, and other MCP clients
without shelling out.

Files:

- Existing: `src/freellmpool/mcp_server.py`
- Existing: `server.json`
- Existing: `docs/MCP.md`
- Existing: `docs/MCP_LISTINGS.md`
- Existing: `docs/mcp-listings/*`
- New from WU-001: `src/freellmpool/tailnet.py`
- New or existing helper modules from WU-004 through WU-010
- Existing tests: `tests/test_mcp.py`, `tests/test_mcp_listings.py`

Implementation:

- Add tools:
  - `free_llm_roles`: list available roles and recommended use
  - `free_llm_recipe`: run a bounded recipe
  - `free_llm_battle`: compare a prompt across a small panel
  - `free_llm_second_opinion`: run the shared small-panel second-opinion flow
  - `free_llm_tailnet_info`: show safe Tailnet connection instructions
  - `free_llm_quota_wise`: show local quota-mode/headroom advice
- Keep tools bounded by existing panel caps and max token clamps.
- Do not add a mutating `set_policy` tool in this plan.
- Update MCP docs, listing copy, and `server.json` descriptions if the public
  tool surface changes. The registry-facing copy must mention only tools that
  are actually exposed by `tools/list`.

Definition of Done:

- `tools/list` includes the new tools.
- Each new tool has input schema tests and fake-provider execution tests.
- Tailnet info never includes provider API keys.
- Quota-wise info uses local counters only and never suggests account rotation
  or limit bypass.
- MCP docs, registry listing drafts, and `server.json` stay in sync with the
  exposed tool names.

Verification:

- `PYTHONPATH=src python3 -m pytest tests/test_mcp.py tests/test_roles.py tests/test_recipes.py tests/test_battle.py`
- `PYTHONPATH=src python3 -m pytest tests/test_mcp_listings.py`

### WU-012: Documentation, Demo Path, and Release Readiness

Purpose: make the new product story obvious from README and docs.

Files:

- Existing: `README.md`
- Existing: `docs/INTEGRATIONS.md`
- Existing: `docs/ROADMAP.md`
- Existing tests/checks: `tests/test_faq.py`, `scripts/check_release_ready.py`

Implementation:

- Rewrite the top README flow around:
  - `freellmpool init`
  - agent profiles
  - Tailnet gateway
  - roles and quota-wise mode
  - battle/playground
  - second-opinion everywhere
  - recipes/jobs/reports
- Update `docs/ROADMAP.md` so user experience work is first-class, with
  reliability work described as support for those workflows.
- Add a short Tailnet guide to `docs/INTEGRATIONS.md`.
- Keep promotional claims conservative: local tool, legitimate free tiers,
  auth-required Tailnet serving, no rate-limit bypass.

Definition of Done:

- README has one copy-pastable path for Tailnet.
- README has one copy-pastable path for Metaswarm.
- Release-readiness checks reflect new public commands.
- Docs link to the relevant tests/features.
- A release check or test guards the stdlib-first contract by flagging new
  runtime dependencies unless the PR includes an explicit architecture note.
- WU-012 resolves the full release smoke environment: the current system
  `twine`/`pkginfo` rejects `Metadata-Version: 2.4` artifacts, so the release
  path must either pin/update packaging tool requirements or make the script
  create a compatible checker environment before full `twine check` is required.

Verification:

- `PYTHONPATH=src python3 -m pytest tests/test_faq.py tests/test_agents.py tests/test_cli.py`
- `PYTHONPATH=src python3 scripts/check_release_ready.py --skip-build`
- Release-only after packaging-tooling fix: `PYTHONPATH=src python3 scripts/check_release_ready.py`

## Dependency Graph

1. WU-001 Tailnet Gateway Mode can start immediately.
2. WU-002 Init Wizard has a detect-only first slice after WU-001. The full
   agent-aware wizard depends on WU-003, WU-004, and WU-010 so it reflects real
   profiles, roles, and quota-wise behavior instead of placeholder prompts.
3. WU-003 Agent Profiles can start immediately and later consume WU-001 Tailnet
   helpers.
4. WU-004 Role-Based Asking can start immediately.
5. WU-005 Battle/Playground depends on WU-006 for the shared panel helper and
   depends on WU-004 only if role shortcuts are used.
6. WU-006 Second-Opinion Everywhere depends on WU-004. Its verification excludes
   `tests/test_battle.py` until WU-005 lands.
7. WU-007 Recipes depends on WU-004 and WU-006.
8. WU-008 Reports depends on WU-005/WU-007 data structures, but its
   `RunRecord` core can start earlier so battle/recipe/job integrations share
   one artifact shape.
9. WU-009 Jobs depends on WU-007 and WU-008.
10. WU-010 Quota-Wise Mode depends on existing quota/routing plus WU-004 roles.
    Its report/cost-audit integration is deferred until WU-008, and battle,
    recipes, jobs, and second-opinion must honor wise mode as they land.
11. WU-011 MCP UX Tools depends on WU-001 and WU-004 through WU-010.
12. WU-012 Docs runs throughout and lands last.

## Milestones

### Milestone 1: Tailnet + First-Run Win

- WU-001 Tailnet Gateway Mode
- WU-002 Init Wizard
- WU-003 Agent Profiles, including Metaswarm
- WU-004 Role-Based Asking
- WU-010 Quota-Wise Mode

User-visible demo:

```bash
freellmpool init --agent metaswarm --tailnet
freellmpool tailnet serve
freellmpool profile doctor metaswarm
FREELLMPOOL_MODE=wise freellmpool ask --role cheap "summarize this"
```

### Milestone 2: Comparison + Second Opinion

- WU-005 Battle CLI and Local Playground
- WU-006 Second-Opinion Everywhere

User-visible demo:

```bash
freellmpool ask --role coder "write a pytest for this function"
freellmpool ask --second-opinion "is this implementation plan sound?"
freellmpool battle "which launch post is strongest?"
freellmpool playground
```

### Milestone 3: Workflows That Produce Artifacts

- WU-007 Recipes
- WU-008 Reports
- WU-009 Jobs

User-visible demo:

```bash
freellmpool recipe run pr-review --input patch.diff
freellmpool jobs add --recipe repo-summary --path src/freellmpool
freellmpool jobs run
freellmpool report last --html --open
```

### Milestone 4: Agent-Native UX

- WU-011 MCP UX Tools
- WU-012 Documentation and release readiness

User-visible demo:

- Claude/Cursor can call `free_llm_recipe`, `free_llm_battle`,
  `free_llm_second_opinion`, `free_llm_quota_wise`, and `free_llm_tailnet_info`
  directly through MCP.

## Review Gates

- Each milestone gets ordinary tests plus a fresh metaswarm review before merge.
- Opus is reserved for final pre-ship review, consistent with the project
  `.metaswarm/external-tools.yaml`.
- Tailnet serving must get a security-focused review before release because it
  intentionally exposes the proxy beyond loopback.
