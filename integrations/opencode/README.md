# freellmpool plugin for OpenCode

See which free provider/model is actually serving you, check usage and rate-limits,
and control routing quality — all from inside [OpenCode](https://opencode.ai).

It talks to a running [freellmpool](https://github.com/0xzr/freellmpool) proxy
(`freellmpool proxy`, default `http://localhost:8080`).

## What it adds

- **`freellmpool_status` tool** — usage and estimated $ saved, per-provider quota /
  rate-limit / cooldown / latency, the current routing mode, and the most-recently
  served provider+model. Pass `verbose: true` for per-model detail. Ask the agent
  things like *"check freellmpool status"* or *"how much quota is left?"*.
- **`freellmpool_models` tool** — lists the model ids the proxy exposes (including the
  routing aliases `auto` / `fast` / `quality` / `fair`).
- **`freellmpool_tokenmax` tool** 🌈 — blast the same prompt to **every** free model at
  once, then the agent synthesizes them all. Ask *"tokenmax: &lt;hard question&gt;"*. While
  the swarm runs, the companion **embedded TUI dashboard** (`../opencode-tui`) throbs a live
  rainbow `TOKENMAXXING` animation with `N/total` progress — install it too for the show.
- **served-model toast** — after each reply, a small toast shows which provider+model
  actually answered. OpenCode itself only knows the alias you picked
  (`freellmpool/auto`), so the real target is read back from the proxy. Silence it with
  `FREELLMPOOL_TOAST=0`.

## Controlling routing (quality)

Routing is chosen by the **model name** in OpenCode's model picker — no extra config:

| Model | Routing |
| --- | --- |
| `freellmpool/spread` | **best for agentic work** — spread across the *whole* pool (least-used tier first → no provider hits its rate limit), with a latency/health tie-break so it stays fast. Use this for long, multi-step loops. |
| `freellmpool/auto` | proxy default (whatever `FREELLMPOOL_ROUTING` is set to) |
| `freellmpool/fast` | lowest-latency provider first (concentrates load → can rate-limit under sustained loops) |
| `freellmpool/quality` | match the model's capability to the prompt's difficulty |
| `freellmpool/fair` | spread load across providers (preserve quota), latency-blind |

**For agentic coding, pick `freellmpool/spread`.** `fast` keeps hitting the same few fast providers
every turn, so they exhaust their free-tier limits first and you get a 429 storm; `spread` rotates
across all of them while still preferring the quick/healthy ones.

(Advanced: send an `X-Freellmpool-Routing: spread|fast|quality|fair` header instead.)

## Install

> **Registry publication status: pending.** `opencode-freellmpool` and its
> companion `opencode-freellmpool-tui` are pack/install/load-tested in CI, but
> neither package was published on npm as of 2026-07-19.
> Repository-local plugin sources are included in 0.11.4; current `main` adds registry-readiness hardening
> and corrected defaults. Use the local-file path below until verified registry
> versions exist.

**Option A — drop-in (no build):**

```sh
mkdir -p ~/.config/opencode/plugin
cp freellmpool.js ~/.config/opencode/plugin/
```

**Option B — reference from your `opencode.json` / `opencode.jsonc`:**

```jsonc
{
  "plugin": ["/absolute/path/to/integrations/opencode/freellmpool.js"]
}
```

Then make sure the proxy is running (`freellmpool proxy`) and that OpenCode has a
`freellmpool` provider pointed at it (`baseURL: http://localhost:8080/v1`). Add the
routing aliases as models so you can pick them:

```jsonc
{
  "provider": {
    "freellmpool": {
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://localhost:8080/v1" },
      "models": {
        "spread": {},
        "auto": {},
        "fast": {},
        "quality": {},
        "fair": {}
      }
    }
  }
}
```

## Configuration (env)

| Variable | Default | Purpose |
| --- | --- | --- |
| `FREELLMPOOL_PROXY_URL` | `http://localhost:8080` | proxy base URL |
| `FREELLMPOOL_PROXY_KEY` | _(none)_ | sent as `Authorization: Bearer <key>` if your proxy requires one |
| `FREELLMPOOL_TOAST` | on | set to `0`/`false`/`off` to silence the served-model toast |

The plugin never throws on a down proxy — the tools return a short message and the
toast hook stays quiet.
