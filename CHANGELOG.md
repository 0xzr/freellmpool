# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Advisory proxy operations APIs: public `/livez` and `/readyz`, an
  authenticated secret-free `/v1/providers` inventory, and
  `/v1/models?ready=true` filtering.
- A print-only Hermes Agent custom-endpoint profile and setup documentation.
- Registry-ready OpenCode server/TUI packages with clean tarball install/load
  validation and a protected manual publication workflow.
- Experimental metaswarm review adapter integration, with setup docs and no-key
  smoke coverage for fail-closed `auth_missing` behavior.
- A dated [live model-activity audit](docs/MODEL_ACTIVITY_AUDIT_2026-07-14.md)
  covering discovery and completion probes across 18 configured providers.
- A dated [provider refresh](docs/MODEL_ACTIVITY_AUDIT_2026-07-16.md) with
  primary sources and explicit live-test caveats.
- An [exhaustive live catalog audit](docs/MODEL_ACTIVITY_AUDIT_2026-07-17.md)
  covering 381 chat routes and every embedding/transcription route that ships.
- Aion Labs, ModelScope API Inference, and SiliconFlow free-tier routes, plus
  OVHcloud's Qwen3 embedding route and LLM7's `gpt-oss:20b` chat route.
- Weekly Dependabot coverage for Python, GitHub Actions, and container images.

### Changed
- Corrected the OpenCode integrations to use the proxy's actual port 8080
  default and refreshed the dated competitor comparison from pinned sources.
- Refreshed the provider catalog to 239 enabled chat routes and 397 cataloged
  chat models, adding live-verified current models and disabling stale, retired,
  empty, degraded, or repeatedly timing-out routes.
- Added six completion-verified CohereLabs routes exposed by the Hugging Face
  router after an exhaustive second-pass catalog-gap probe.
- Updated CI action majors and made the Docker image non-root and health-checked.
  Docker Compose now binds to localhost, enables Open WebUI authentication by
  default, and pins Open WebUI to a released version.
- Reconciled live catalog state after repeat probes: added Kilo Kat Coder and two
  NVIDIA routes, re-enabled six recovered routes, and disabled retired,
  degraded, or repeat-timeout Ollama, GitHub, and NVIDIA routes.

### Fixed
- Public README, integration, agent, and Pages documentation now distinguishes
  released 0.11.4 from post-tag changes on `main`; the jailed OpenCode launcher
  also uses the proxy's actual port 8080 default.
- The catalog vetter now filters paid Kilo/OpenRouter/OpenCode discovery rows,
  requires non-empty completion text, gives reasoning models enough output
  budget to answer, and classifies 410/retirement responses correctly.
- External catalog sync no longer follows redirects, closing a server-side
  redirect-to-private-network path.
- The published container command now passes the proxy's explicit LAN and
  no-auth acknowledgements, so its documented keyless local startup works.
- NVIDIA embeddings now send the required `input_type`, and embedding and
  transcription routing now skips catalog models marked disabled.
- Catalog-vetter classification recognizes GitHub's `Unknown model` response as
  a definitive dead-model result.

## [0.11.4] — 2026-06-17

MCP Registry verification metadata release.

### Added
- Hidden `mcp-name: io.github.0xzr/freellmpool` README metadata so the official
  MCP Registry can verify PyPI package ownership.
- Test coverage that `freellmpool tokenmax --timeout` is also honored by the
  synthesis request.

## [0.11.3] — 2026-06-11

Launch polish and packaging metadata release.

### Added
- A CI-backed 30-second quickstart check that installs from a clean venv and
  requires a keyless first reply.
- `FAQ.md` with prompt-destination, privacy, ToS, failover, ban-risk, and
  comparison answers.
- Above-the-fold README demo assets for `tokenmax`, plus an upload-ready GitHub
  social preview asset and local GitHub discovery checklist.
- Contributor templates, PR template, and ready-to-file good-first-issue drafts
  for newcomer-sized tasks.
- Release checklist for the operator-run tag/build/publish flow.
- `freellmpool ask --timeout` and `freellmpool tokenmax --timeout` so slow
  upstreams can be bounded explicitly from the CLI.

### Changed
- Public provider/model counts are now checked from the provider catalog by
  `scripts/check-counts`, reducing drift across README/docs/release metadata.
- README comparison copy is more explicit and gracious about OpenRouter,
  LiteLLM, and FreeLLMAPI.
- PyPI metadata now has a shorter launch description, broader keywords,
  project URLs, and more complete classifiers.

## [0.11.2] — 2026-06-10

### Fixed
- The local proxy now aligns its listen backlog with its 128-request worker cap,
  so short mixed-traffic bursts queue cleanly instead of occasionally resetting
  connections before the proxy can accept and handle them. The existing hard cap
  still bounds worker threads and process resources under sustained floods.

## [0.11.1] — 2026-06-10

Hardening and operations release after the 0.11 capacity tooling.

### Added
- **`freellmpool doctor`** — a no-network local diagnostics command that reports
  package version, config paths, configured provider count, routing mode,
  quota/cache paths, external catalog cache age, and bundled catalog validity.
- **Local catalog validation and hot-path benchmarks.** `scripts/validate_catalog.py`
  validates bundled provider metadata in CI, while `scripts/bench_hotpaths.py`
  and `scripts/compare_benchmarks.py` track routing/cache/quota hot paths.
- CI now runs catalog validation, focused mypy checks, coverage with a minimum
  threshold, and a built-wheel smoke test including `freellmpool doctor`.

### Changed
- Routing now normalizes mode names consistently and indexes provider/model
  targets so large catalogs avoid repeated scans and metric lock churn.
- Response cache keys include the active routing mode, preventing cross-mode
  cache hits between `fast`, `quality`, and `fair` routing.
- Cache storage uses SQLite WAL mode, prunes expired rows, and supports a
  `FREELLMPOOL_CACHE_MAX_ENTRIES` cap.
- Quota counters can batch writes with `FREELLMPOOL_QUOTA_FLUSH_EVERY=N` while
  still flushing on snapshots and shutdown paths.

### Fixed
- Sync and async HTTP transports now honor bounded retries with `Retry-After`
  support without losing the last provider response when the retry delay consumes
  the request deadline.
- POST retries no longer replay read-phase transport errors after a request may
  have reached the provider; only connect/pool-acquisition failures are retried.
- The async transport now streams and caps response bodies like the sync path,
  enforcing response-size and wall-clock deadline guards.
- Async retry attempts keep the original request headers instead of accidentally
  reusing a provider response's headers.
- Cache, quota, and stats persistence paths handle disk/SQLite failures as
  best-effort operations instead of crashing hot paths.

## [0.11.0] — 2026-06-06

Capacity management — see what's usable right now and keep your free tiers
healthy. From [#7](https://github.com/0xzr/freellmpool/pull/7) by
[@arthurlacoste](https://github.com/arthurlacoste), with maintainer hardening.

### Added
- **`freellmpool capacity status`** — a local-first summary of every provider:
  configured vs missing a key, enabled-model count, today's usage against the
  daily quota hint, and a `healthy` / `low_quota` / `exhausted` / `invalid_key` /
  `missing` status. `--target N` flags when you're below N healthy providers;
  `--all` includes missing ones and external-only candidates. See
  [docs/CAPACITY.md](docs/CAPACITY.md).
- **`freellmpool providers health`** — sends one tiny request to each configured
  provider and reports latency / failure (so you can tell a missing key from a
  rate-limited or down provider). `-p` to filter, `--timeout`, `-m` to pin a model.
- **`freellmpool keys status` / `keys checklist` / `keys add`** — an optional,
  metadata-only key inventory (`~/.config/freellmpool/keys.toml`; never stores raw
  secrets) plus an interactive `keys add` that writes the key to `config.toml`,
  records metadata, and can create a provider — matching a typo or model name
  against the external catalog, or building an OpenAI-compatible stub and
  autodiscovering its models from `GET /models`.
- **`freellmpool catalog sync` / `catalog status`** — sync an advisory external
  provider catalog ([mnfst/awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis))
  into a local cache to surface free providers you could add next. Advisory only:
  the executable routing config remains `providers.toml`; the network sync falls
  back to the cache when offline.
- **Dashboard capacity panel** — the proxy `/dashboard` now shows a healthy-provider
  count and a per-provider capacity/usage table.
- **Context-aware failover.** When a model rejects an input as too long, freellmpool
  learns that model's window (and honors an optional `context = N` hint per model in
  `providers.toml`), stops routing oversized requests to models it knows can't fit,
  and raises a clear `ContextWindowExceeded` (estimated input size included; a `413`
  over the proxy) instead of a generic exhaustion. It never truncates your input.

### Changed
- `benchmark` failures now include the exception message, not just its type.

### Hardening (maintainer follow-up)
- `toml_escape` escapes control characters, so an external-catalog value
  containing a newline can't corrupt the generated user `providers.toml`.
- Base URLs that become routing targets are validated before use: the external
  import is https-only; the user-provided stub requires an http(s) scheme (http
  kept for localhost) and rejects whitespace/control characters; model discovery
  fetches http(s) only (no `file://`).
- External-catalog and model-discovery downloads are size-capped.

### Robustness (full-codebase audit)
- **Never crash on a provider's response shape.** OpenAI-style list `content`
  (content-parts), `null` content, and malformed `choices`/`message`/Gemini parts
  are coerced or rejected cleanly instead of throwing — so a valid completion is
  no longer discarded and the provider penalized.
- **A typo in the user `providers.toml` no longer bricks the tool.** Catalog
  parsing is tolerant: a bad row (missing id/name/base_url, non-int `rpd`/`context`)
  is skipped and a broken-TOML user catalog is ignored, instead of an uncaught
  traceback across every CLI command, the proxy, and MCP. `context = 0` reads as
  unknown, not "too long".
- **CLI no longer dumps a traceback on EOF/Ctrl-D** (piped/CI stdin) in the
  interactive `keys add` flow.
- Learned context limits ignore implausibly-small figures (can't be poisoned by
  one bad error); the input estimate now counts `tool_calls`.
- **Security:** the proxy auth compare is constant-time (`hmac.compare_digest`);
  the key inventory writes secrets at `0o600` atomically (no world-readable
  window); model discovery no longer follows redirects (can't forward the Bearer
  key); `sync_external_catalog` validates the source scheme.
- The proxy caps concurrent connections (slowloris-resistant), rejects truncated
  bodies, won't write an HTTP status line into an open SSE stream, and emits a
  string (never `null`) for Responses `output_text`. The response cache prunes
  expired rows and refuses to cache non-JSON requests. The async path off-loads
  blocking quota/cache I/O.

### Catalog
- Re-vetted the live model set against every provider: 22 models that moved to a
  paid tier (402), were removed (404), or became tier-gated (403) are now
  `enabled = false` (still callable when pinned); 8 that recovered are
  re-enabled; added NVIDIA's `nemotron-3-ultra-550b-a55b` (on NVIDIA NIM and
  OpenRouter). 223 of 331 catalog entries are auto-routable. SambaNova's free
  chat tier is gone (all models 402) — kept in the catalog, off.
- New maintainer tool `scripts/vet_catalog.py`: lists each provider's live
  `/models`, diffs it against the catalog, and pings every entry through the
  real client to flag dead vs rate-limited models.

## [0.10.1] — 2026-06-03

A full-project review (Codex + a manual pass), reconciled to consensus, plus the
last of the rename.

### Changed
- Renamed the base exception `BuffetError` → `FreeLLMPoolError` (the last vestige
  of the old `llmbuffet` name). `BuffetError` stays as a deprecated alias for now.

### Fixed
- **Proxy resource safety.** Request sockets now have a read timeout (75s) and
  worker threads are daemons, so a slow/stalled client can't pin a thread+fd or
  block shutdown.
- **Streaming + tools no longer drops `tool_calls`.** A `stream:true` request
  that asked for tools now emits the tool calls (each with the per-call `index`
  OpenAI streaming requires) and a `tool_calls` finish reason instead of
  silently returning an empty `stop`.
- **Mid-stream upstream errors** no longer try to write a JSON 500 into an open
  SSE response; the event stream is closed cleanly.
- **`content: null`** on assistant tool-call turns is no longer stringified to
  the literal `"None"`, which had corrupted multi-turn tool history.
- **Embeddings** honor a pinned `provider/model` (or `provider`) id instead of
  ignoring any model containing `/`.
- **Anthropic `/v1/messages`** validates the request and returns an
  Anthropic-shaped error envelope (not an OpenAI one) on bad input.
- **Quota counters are cross-process safe.** `record()` reloads under a POSIX
  file lock before writing, and `snapshot()` re-reads, so a proxy + CLI + MCP
  server sharing one quota file no longer clobber each other's increments.
- **MCP JSON-RPC conformance.** Parse errors (-32700), invalid requests
  (-32600), and batch requests are handled per spec (a batch returns one JSON
  array of responses) instead of being dropped.
- **SQLite cache connections are explicitly closed** (`contextlib.closing`) —
  `with sqlite3.connect()` only manages the transaction, so each get/put had
  been leaking a file handle until GC.

## [0.10.0] — 2026-06-03

### Added
- **Async API.** `from freellmpool import AsyncPool` — `await pool.aask(...)` /
  `await pool.achat(...)` over `httpx.AsyncClient`, with the same failover,
  cooldown, quota, and metrics as the sync `Pool`. Use it as an async context
  manager (`async with AsyncPool.from_default_config() as pool:`). Imported lazily
  so the sync path never pulls in the async stack.
- **Per-provider metrics + metrics-aware routing.** Every call records latency
  (EWMA) and success/failure per `provider/model`. Default `fair` routing now
  sinks a currently-failing target to the back; opt into `fast` routing
  (`FREELLMPOOL_ROUTING=fast` or `routing="fast"`) to prefer the lowest measured
  latency. The dashboard shows a "measured latency" table.
- **`freellmpool benchmark`** — times one call per configured provider
  concurrently and prints a latency/success table (and warms the routing
  metrics). `-m` to pin a model, `-p` to filter providers.
- **Observability hooks.** Pass `on_event=...` to `Pool`/`AsyncPool` to receive
  structured event dicts (`attempt`/`success`/`error`/`cooldown`/`exhausted`) for
  tracing/metrics. Set `FREELLMPOOL_LOG=info|debug` to log them from the CLI/proxy.
  The library never configures logging handlers itself.
- **Plugin system.** `register_provider(...)` adds a custom endpoint to the
  routing catalog; `register_adapter(name, fn)` teaches the client a new request
  shape. Providers can also be contributed via a `freellmpool.providers` entry
  point (discovered lazily; a broken plugin is skipped, never fatal).

### Hardening (post-review)
From a Codex adversarial review of the above:
- Client/capability errors (4xx other than 408/429 — bad request, auth, 402
  payment, unknown model, gemini "tools unsupported") no longer count against a
  target's health metrics; only availability failures (429/5xx/network) do, so a
  tool request can't poison routing for later non-tool traffic.
- `AsyncPool` now routes plugin-registered adapters through the adapter registry
  (via a worker thread), matching the sync `Pool`, and applies the response cache
  on the async path.
- The async `httpx.AsyncClient` is created under an async lock and rebound per
  event loop (no leaked clients on concurrent first calls or reuse across
  `asyncio.run`).
- Stats counters update under a lock (the proxy is multi-threaded); each target's
  cooldown state is read once per request so a concurrent 429 can't
  double-schedule it; plugin providers merge by id; entry-point loading is locked
  so no reader sees a partial list.
- `fast` routing no longer prefers an unmeasured provider over a measured-fast one
  (unknown targets get a neutral baseline: behind healthy, ahead of failing).

## [0.9.3] — 2026-06-03

### Fixed
- Thread-safe lazy init of the pooled httpx client (double-checked lock). Under
  the threaded proxy, two concurrent first requests could each create a client
  and orphan one. Also registers `atexit` close for graceful FD cleanup. (Found
  in a Codex review of the 0.9.2 pooling change.)

## [0.9.2] — 2026-06-03

### Changed (performance)
- **Connection pooling.** Requests now reuse a process-wide keep-alive httpx
  client instead of opening a fresh TCP+TLS connection per call. Big latency win
  for repeated calls to the same provider (agent loops, the proxy): ~0.15s/call
  warm vs a full handshake each time.
- **Fast-fail connect timeout (10s)** so a dead/unreachable provider fails over
  quickly instead of waiting the full read timeout.
- `_order` snapshots quota once per request instead of a locked read per
  candidate — matters now that the catalog has 300+ models.

## [0.9.1] — 2026-06-03

### Changed
- **Every model live-validated; broken ones off by default.** All 324 catalog
  models were reachability-tested; 94 that failed (404/403/402, persistent
  errors, timeouts) are now `enabled = false` — skipped by auto-routing so users
  don't hit dead models, but still callable when pinned explicitly (`-m`).
  `Model.enabled` added; `freellmpool providers` shows `N models (+M off)` and
  `freellmpool models --all` lists the disabled ones.

### Fixed
- Reasoning-model token floor lowered from 8192 to 4096 — 8192 exceeded some
  providers' caps (e.g. Groq's gpt-oss), which made those models error/return
  empty. Groq's `gpt-oss-120b`/`-20b` work again.

## [0.9.0] — 2026-06-03

### Added
- **Catalog expanded from 56 to 300+ chat models.** Model lists are now
  discovered from each provider's `/models` endpoint and filtered to chat models
  (embeddings/rerank/audio/safety models excluded). The embedder catalog grew to
  23. Not every advertised model is callable on every free tier — freellmpool
  fails over.
- **`free_llm_quota` MCP tool** — shows today's per-provider usage, daily-limit
  headroom, session totals, and estimated cost avoided.

## [0.8.1] — 2026-06-03

### Fixed
- Streaming connection lifecycle: the httpx stream/client is now always closed —
  on non-200 failover, on early client disconnect, and on exhaustion (was leaking
  sockets/fds on exactly the failover paths streaming exercises).
- Proxy rejects negative `Content-Length` and caps request bodies at 16 MB.
- Model names that contain `/` but aren't provider-prefixed now route correctly
  (e.g. a client sending `openai/gpt-oss-120b` as the model).
- Response cache key now includes `tool_choice`.
- 429 cooldown is stamped with a fresh timestamp instead of the request-start time.
- The Anthropic `/v1/messages` shim no longer 500s on malformed input (bad
  `max_tokens`/`temperature`, null text blocks).
- `/dashboard` and `/v1/models` are gated behind the proxy key when one is set.
- Responses tolerate broken pipes; reasoning models (incl. `gpt-oss`) get token
  headroom so they don't return empty content.

### Changed
- Rewrote the README and docs in a plainer style (less emoji, less marketing).

## [0.8.0] — 2026-06-03

### Added
- **Anthropic Messages shim (`/v1/messages`)** — run **Claude Code** (and any
  Anthropic-API tool) on free models via `ANTHROPIC_BASE_URL`. Translates text +
  tools (tool_use/tool_result) and emits Claude's exact streaming event sequence;
  `claude-*` model names auto-route to free models. `freellmpool code claude`
  prints the setup. Experimental (no vision yet). Live-verified end to end.
- **Response caching (sqlite, opt-in).** Set `FREELLMPOOL_CACHE_TTL` (or
  `[settings] cache_ttl`) to cache identical requests — saves quota on dev/test
  loops and answers instantly. Off by default.
- **Web dashboard** at **`/dashboard`** — a self-contained page showing
  configured providers, today's per-provider usage, requests served, cache hits,
  and "$ not paid to OpenAI". Auto-refreshes.

## [0.7.0] — 2026-06-03

### Added
- **True token streaming.** The proxy now streams tokens from the provider in
  real time (`stream: true`) instead of buffering — with failover *before* the
  first byte. `Pool.stream_chat()` exposed for library use. Live-verified (15
  incremental chunks on Groq). Tool-calling requests still use the buffered path.
- **`freellmpool code <agent>`** — prints one-command setup to wire a coding
  agent (codex, aider, cline, continue, cursor, opencode) to the free proxy.

## [0.6.0] — 2026-06-03

### Added
- **MCP server** — `freellmpool mcp` runs a Model Context Protocol server over
  stdio (zero extra deps), so **Claude Desktop / Claude Code / Cursor** can
  offload subtasks to free models. Tools: `free_llm_ask`, `free_llm_models`.
  Works with no API keys. See [docs/MCP.md](docs/MCP.md). Live-verified end to end.

## [0.5.0] — 2026-06-03

### Added
- **Pooled free embeddings** — `pool.embed(...)` and a proxy `/v1/embeddings`
  route over Cohere / GitHub Models / Cloudflare / Mistral / NVIDIA free tiers,
  with failover. (Free RAG, not just chat.) Live-verified.
- **`llm-freellmpool` plugin** for Simon Willison's [`llm`](https://llm.datasette.io)
  CLI — `llm install llm-freellmpool` → `llm -m freellmpool "..."` (zero keys).
- **`config.toml`** support: `[keys]` (filled under env), `[aliases]`, `[settings]`
  (`cooldown_seconds`, `proxy_key`). See `config.toml.example`.
- **Docker**: image + GHCR build/push workflow + `docker-compose.yml` (freellmpool
  + Open WebUI). Built and run-tested locally.
- **"$ saved vs OpenAI" metric** — `freellmpool ask -v` and the proxy shutdown
  line show avoided GPT-4o cost.
- README repositioned around the developer wedge (CLI + library + proxy + `llm`
  plugin, keyless) with a "correct by design" section; terminal demo at the top.

## [0.4.0] — 2026-06-03

### Added
- **Model aliasing.** Common OpenAI/Anthropic names (`gpt-4o-mini`, `gpt-4o`,
  `claude-3-5-sonnet`, …) auto-resolve to free models, so existing code runs
  against freellmpool unchanged. Override with `FREELLMPOOL_ALIAS_<name>=...`.
- **Tool / function-calling passthrough.** `tools` / `tool_choice` are forwarded
  to providers that support them and `tool_calls` are returned — unlocking
  aider, Continue, and other agentic tools. Live-verified on Groq.
- **Observability headers** on proxy responses: `X-Freellmpool-Provider`,
  `X-Freellmpool-Model`, `X-Freellmpool-Attempts`.
- **[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)** — copy-paste setup for opencode,
  aider, Continue, Cline, Cursor, Open WebUI, LibreChat, LangChain, LlamaIndex,
  Vercel AI SDK, `llm` CLI, shell-gpt, n8n, and more.

## [0.3.0] — 2026-06-03

### Changed
- **Renamed `llmbuffet` → `freellmpool`** (clearer, keyword-rich, no name
  collision). Python API is now `from freellmpool import Pool`; CLI is
  `freellmpool` (with `ffp` as a short alias); config lives under
  `~/.config/freellmpool/`; env vars are `FREELLMPOOL_*`.

### Added
- **Codex / Responses API shim** — the proxy now serves `POST /v1/responses`
  (non-streaming + typed SSE events), so OpenAI Codex CLI and other
  Responses-based agents can run on pooled free inference.
- **Pollinations** — a second keyless provider (16 providers / 56 models total),
  strengthening the zero-config path.
- Agent docs for Codex CLI in `docs/AGENTS.md`; honest **Limitations** section
  in the README.

## [0.2.0] — 2026-06-03

### Added
- **Six more providers** (15 total / 53 models): NVIDIA NIM, OVHcloud AI
  Endpoints, LLM7, Ollama Cloud, Z.ai/Zhipu GLM, LongCat; expanded model lists
  for Groq, Cerebras, OpenRouter, GitHub Models, SambaNova, Mistral, Gemini.
- **Keyless / zero-setup providers.** OVHcloud works with no API key
  (anonymous); LLM7's key is optional. `pip install freellmpool && freellmpool ask`
  now works with no signup at all. Catalog gains `auth` and `key_optional`.
- **Model selection.** New `freellmpool models` lists every `provider/model` id;
  `ask -m provider/model` pins an exact model on an exact provider.
- **Streaming proxy.** The proxy honors `stream: true` with a buffered
  OpenAI-style SSE stream, so stream-only clients (chat UIs, agents) work.
- **429 cooldown.** A rate-limited provider is deprioritized for a cooldown
  window instead of being retried immediately.
- **Reasoning-model handling.** Thinking models get a `max_tokens` floor and
  `<think>…</think>` blocks are stripped from output.
- `freellmpool ask --json` requests JSON and strips code fences.

### Hardening (post-review)
- Proxy now validates all request fields and returns OpenAI-style `400`s for
  malformed input; a catch-all ensures no request can kill a server thread.
- Optional proxy auth: `--api-key` / `FREELLMPOOL_PROXY_KEY` requires a Bearer
  token; a warning fires when binding to a non-loopback host without one.
- Quota store is now thread-safe (lock + unique temp file) and best-effort, so
  a persistence hiccup can't abort a successful completion.
- A provider that returns `429` has its remaining models skipped for that
  request; cooldowns update under a lock with `max()`.
- Verified live against 11 providers + the OpenAI SDK (non-streaming & SSE).
  Fixed the LongCat model id (`LongCat-2.0-Preview`); LLM7 leads the keyless
  pool (most reliable zero-key provider).

## [0.1.0] — 2026-06-02

Initial release.

### Added
- Provider catalog (`providers.toml`) covering 9 free-tier providers and 24
  models: Groq, Cerebras, OpenRouter, Google Gemini, GitHub Models, Cloudflare
  Workers AI, Mistral, Cohere, SambaNova.
- Quota-aware, least-used-first router with automatic failover across providers.
- Persistent per-provider/day quota tracking (`~/.config/freellmpool/quota.json`,
  resets at UTC midnight).
- OpenAI-compatible proxy server (`freellmpool proxy`) exposing
  `/v1/chat/completions` and `/v1/models` — a drop-in `OPENAI_BASE_URL`.
- CLI: `ask`, `providers`, `quota`, `proxy`.
- Python API: `from freellmpool import Pool`.
- Three request/response adapters (openai, gemini, cloudflare) and per-user
  catalog overrides via `~/.config/freellmpool/providers.toml`.
- Full unit-test suite with a faked transport (no network) and CI on Python
  3.11–3.13.
