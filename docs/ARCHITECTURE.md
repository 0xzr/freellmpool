# Architecture

freellmpool is a local gateway built around a packaged catalog,
credential-aware configuration, an eligibility-aware router, bounded provider
clients, and thin CLI/proxy/MCP interfaces. The current catalog contains 22
provider groups, 431 chat models, and 178 enabled chat routes. Catalog presence
is not routing eligibility: recurring free tiers, keyless endpoints, finite
trials, pin-only routes, and disabled candidates remain distinct.

```text
CLI / Python / MCP / OpenAI, Responses, or experimental Anthropic clients
                              |
                              v
                    Pool (router.py)
       configured/keyless providers + enabled automatic targets
       exact pins may select an enabled pin-only target
                              |
             quota + route health + conformance + metrics
                              |
                              v
                   bounded client dispatch
        OpenAI-shaped adapter | Gemini adapter | plugins
                    /              |              \
                 chat         embeddings       transcription
```

## Configuration and eligibility

1. `config.py` loads the packaged `providers.toml` and merges an optional user
   provider catalog (`$FREELLMPOOL_CONFIG` or the default config directory).
2. Environment variables override keys stored under `[keys]` in
   `config.toml`; required extra fields such as the Cloudflare account ID are
   checked too. Keyless and key-optional providers can be configured without a
   credential.
3. `Pool` builds automatic candidates only from models with both `enabled =
   true` and `auto = true`. An exact provider/model pin may select an enabled
   `auto = false` route, but never a disabled one.
4. Chat, embedding, and transcription catalogs are separate. Their route
   counts must not be treated as interchangeable.
5. `freellmpool local discover` performs an explicit, bounded preview of a
   fixed list of local runtimes, or one user-supplied canonical literal-loopback
   URL. `local import --yes` writes only pin-only routes to the user catalog;
   `local remove --yes` reverses only blocks managed by that import.

Provider base URLs, IDs, model names, and environment-variable names are
validated before use. Public provider credentials are never sent through
redirects, and private/loopback provider URLs require an explicit
local-provider opt-in.

The local-runtime path is intentionally narrower: it sends no credentials,
follows no redirects, performs no DNS, LAN, or process scan, and accepts only
canonical literal loopback addresses. Imported routes are never added to
automatic routing. `freellmpool doctor` also performs a strict, secret-safe
validation pass over the otherwise tolerant config loader; syntax and table
type errors include location/type metadata without echoing config values.

## Main modules

| Module | Responsibility |
|---|---|
| `config.py`, `models.py`, `providers.toml` | Catalog parsing, user overrides, credentials, and route metadata. |
| `router.py`, `routing_modes.py`, `capability.py`, `task_quality.py` | Candidate filtering, exact pins, route ordering, task/capability matching, and failover. |
| `quota.py`, `metrics.py`, `route_health.py`, `conformance.py`, `readiness.py` | Local daily hints, latency/failure evidence, persistent circuits, protocol evidence, and advisory readiness. |
| `client.py`, `aio.py` | Bounded sync/async HTTP dispatch, OpenAI/Gemini request shapes, streaming, embeddings, and transcription. |
| `proxy.py`, `anthropic_shim.py` | OpenAI Chat/Responses, experimental Anthropic Messages, embeddings, transcription, models/providers/status/readiness endpoints, dashboard, and playground. |
| `cli.py`, `profiles.py`, `agents.py`, `tailnet.py` | User commands, agent configuration guidance, profile diagnostics, and authenticated Tailnet setup. |
| `mcp_server.py`, `panel.py`, `battle.py`, `recipes.py`, `roles.py`, `jobs.py`, `tokenmax.py` | MCP tools, multi-model orchestration, durable jobs, and bounded fan-out. |
| `cache.py`, `stats.py`, `observe.py`, `artifacts.py`, `reports.py` | Optional response cache, persistent aggregate stats, secret-safe events, and local artifacts/reports. |
| `capacity.py`, `healthcheck.py`, `key_inventory.py`, `catalog.py`, `catalog_validation.py` | Local capacity views, bounded canaries, key metadata, external-catalog assistance, and catalog invariants. |
| `local_runtime.py` | Explicit, bounded loopback runtime discovery and reversible pin-only user-catalog imports. |
| `plugins.py` | Custom providers and request adapters without changing the built-in client. |

## Routing and failure handling

For an unpinned chat request, the pool starts with enabled automatic targets
available from the current configuration. Context limits, requested protocol
features, persistent route circuits, provider cooldowns, local quota hints, and
the selected routing mode refine their order:

- `fair` balances by provider and then model so wide catalogs do not dominate.
- `fast` prefers measured latency and health.
- `quality` matches prompt difficulty and bounded task evidence to capability.
- `spread` uses coarse usage tiers, then prefers fast/healthy targets within a
  tier for sustained aggregate capacity.
- `agent` stays in the strongest available capability tier, then spreads usage
  and prefers fast/healthy targets.
- `legacy`, `model`, and `model-fast` retain per-target compatibility modes.

The `wise` operating mode may further narrow routing based on local headroom.
Local `rpd` values are advisory request-count hints, not provider entitlements
or monetary guarantees. freellmpool's counters roll over at UTC midnight;
upstream providers use their own limit and reset windows.

Each non-streaming candidate gets at most two bounded attempts for retryable
transport errors or HTTP 408/429/5xx responses inside the caller's deadline,
honoring `Retry-After` only when feasible. If the candidate still fails, the
router records a normalized failure, updates cooldown/circuit state, and
advances to the next target. A stream can fail over only before the downstream
event stream is committed. Once headers or events are sent, freellmpool never
replays the request on another provider; a later failure uses protocol-specific
error framing when possible and never emits a successful terminal event.

Text-only OpenAI Responses and Anthropic Messages streams use the same
incremental upstream path as streaming chat. Their first usable delta is
obtained before downstream commit, then subsequent deltas are relayed in
protocol order. Tool calls and richer content remain on a buffered
compatibility path so partially translated structures are never exposed.

On success, the pool records local quota, latency, health, and aggregate token
statistics. It stores only normalized operational evidence; prompts, responses,
authorization headers, and raw credentials are excluded from those stores.

## Proxy and persistence

The stdlib HTTP proxy serves `/v1/chat/completions`, `/v1/responses`, the
experimental `/v1/messages`, `/v1/embeddings`,
`/v1/audio/transcriptions`, `/v1/models`, `/v1/providers`, `/status`, `/livez`,
`/readyz`, `/dashboard`, and `/playground`. Inventory and usage endpoints are
authenticated when a proxy key is configured; liveness/readiness remain safe
for orchestration.

`/dashboard` and `/playground` serve one public, self-contained, data-free
browser shell. It fetches usage, inventory, ready models, and battle results
only through bearer-header requests to the protected APIs. After submission,
the input is cleared and the bearer remains only in a JavaScript closure: it is
not placed in URLs, cookies, Web Storage, rendered DOM, globals, logs, or the
HTML response. Reloading the page forgets it and requires authentication again.

`freellmpool capacity status` is an offline/cache-first view and never refreshes
the advisory external catalog unless the user supplies `--refresh`. A refresh
failure falls back to the existing cache, so local readiness and quota views
remain available without network access.

Persistent local state lives under `~/.config/freellmpool` by default.
Container deployments should mount that directory; the repository's
`docker-compose.yml` does so with the `freellmpool-data` named volume and waits
for the proxy health check before starting Open WebUI.

## Design boundaries

- The required runtime dependency remains `httpx`; most surfaces use the Python
  standard library.
- Dependency injection keeps transports, clocks, quota stores, and event hooks
  deterministic in tests.
- freellmpool reacts to provider limits; it does not bypass quotas, rotate
  accounts, or promise that a cataloged route is free or currently available.
- The proxy is intended for local or single-operator use. Bind to loopback by
  default and require `FREELLMPOOL_PROXY_KEY` before intentional exposure.
