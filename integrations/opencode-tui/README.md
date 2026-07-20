# freellmpool dashboard for OpenCode (embedded TUI)

A live, themed dashboard that renders **inside** [OpenCode](https://opencode.ai) — in the
session sidebar and on the home screen — showing, in real time, how much your free
[freellmpool](https://github.com/0xzr/freellmpool) pool is doing for you:

```
┌─ freellmpool ─────────────────────┐
│ routing quality                   │
│ 💸 $0.0006 saved ⚡ 128 tok/s     │
│ 50 tokens served free · 13 req    │
│ ── provider race ──               │
│ 🥇 llm7       ██████████ 9 ⏳53s  │
│ 🥈 ovh        ████░░░░░░ 4        │
│ 🥉 github     █░░░░░░░░░ 1/3600   │
│ latency ███████▇▇▇▇▇ 124ms        │
│ last: github/codestral-2501       │
└───────────────────────────────────┘
```

- **routing mode**, **$ saved**, **tokens served free**, and live **throughput (tok/s)** —
  the totals are **lifetime aggregates** (persisted, summed across the proxy + CLI + MCP,
  so they survive restarts and count *all* freellmpool usage, not just this OpenCode session)
- a medal'd **provider race** — requests used today vs daily cap, with live cooldown timers
- a **latency sparkline** (best provider EWMA over time)
- the **most-recently-served** provider/model
- a **🌈 live TOKENMAXXING animation** — when a `tokenmax` swarm is in flight (see below),
  the panel throbs a color-cycling `🌈 T O K E N M A X X I N G 🌈` banner with an
  `N/total models` progress bar, then flashes a completion line. This is the genuine
  in-harness graphic that MCP can't render — it works here because the plugin draws real
  OpenTUI, not text in a tool result.
- updates every 1.5s by polling the proxy's `/status` (every 0.3s while a swarm runs);
  shows an offline banner if the proxy is down

## 🌈 TOKENMAXXING — the live rainbow

Install the companion tools plugin in `../opencode` and ask OpenCode to use the
`freellmpool_tokenmax` tool (e.g. *"tokenmax: what's the best…"*). It blasts your prompt to
**every** free model at once via the proxy's `/tokenmax` endpoint; while the swarm drains,
**this dashboard throbs the rainbow animation live** and the model synthesizes every answer.
The flashing is a real terminal animation — to also get it standalone, run
`freellmpool tokenmax "…"` in any terminal.

It's a real OpenTUI/SolidJS plugin (not text in a tool result), so it's themed to match
your editor and lives alongside OpenCode's own Context / MCP / LSP panels.

## Install

> **Registry publication status: pending.** `opencode-freellmpool-tui` and the
> companion `opencode-freellmpool` server plugin are pack/install/load-tested
> in CI, but are not yet verified on npm. Use the local-file command below until
> that status changes.

OpenCode TUI plugins are installed with the built-in installer (which wires the OpenTUI
runtime for you — there are no `node_modules` to manage):

```sh
# from a clone of the freellmpool repo:
opencode plugin -g file:/absolute/path/to/integrations/opencode-tui
```

That records the plugin in `~/.config/opencode/tui.json`. Start the proxy
(`freellmpool proxy`) and launch `opencode` — the panel appears on the home screen and in
the session sidebar.

> TUI plugins are configured in `tui.json`, **not** the `plugin` array in `opencode.json`
> (that array is for server/event-hook plugins, like the companion `opencode-freellmpool`
> tools plugin in `../opencode`).

To remove it, delete the entry from `~/.config/opencode/tui.json`.

## Configuration (env)

| Variable | Default | Purpose |
| --- | --- | --- |
| `FREELLMPOOL_PROXY_URL` | `http://localhost:8080` | proxy base URL the dashboard polls |
| `FREELLMPOOL_PROXY_KEY` | _(none)_ | sent as `Authorization: Bearer <key>` if your proxy requires one |

## Controlling routing

Switch OpenCode's model to `freellmpool/fast`, `/quality`, or `/fair` to change routing;
the dashboard's `routing` line reflects the active mode. See the proxy README for what each
mode does.

## Requirements

OpenCode ≥ 1.14 (the embedded-TUI plugin API). The OpenTUI runtime (`@opentui/solid`,
`solid-js`) is provided by OpenCode at load time via its runtime-plugin support — this
package intentionally ships no runtime dependencies.
