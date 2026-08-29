# MCP server — give Claude (and other MCP clients) free models

`freellmpool mcp` runs a [Model Context Protocol](https://modelcontextprotocol.io)
server over stdio, so an MCP client — **Claude Desktop, Claude Code, Cursor**, … —
can hand off self-contained subtasks (drafting, summarizing, classifying, quick
lookups) to **free** LLMs instead of spending its own context/quota.

It needs **no extra dependencies** and can start without API keys when a keyless
provider is available. Add keys to unlock more capacity.

The stdio transport is newline-delimited JSON-RPC 2.0. It is not LSP-style
`Content-Length` framing; each request and response is one JSON object per line,
and `stdout` is reserved for the protocol.

## Tools it exposes

| Tool | What it does |
|---|---|
| `free_llm_ask` | Ask a free model (`prompt`, optional `system` / `model` / `provider` / `routing` / `task` / `max_tokens`). `task` accepts `auto`, `general`, or `grounded-reading`. The reply names the serving model. |
| `free_llm_panel` | Ask the **same** prompt to 2-5 different free models at once and compare. Optional `synthesize` merges them into one best answer; synthesis failure leaves the individual answers visible. |
| `free_llm_second_opinion` | Agent-facing second-opinion surface. Same panel behavior as `free_llm_panel` — exposed as its own tool so callers can declare intent (`prompt`, `n`, `synthesize`, `routing`, `max_tokens`). |
| `free_llm_battle` | Bounded multi-model comparison rendered as a Markdown table (`prompt`, `n`, `synthesize`, `routing`, `max_tokens`). Per-model failures stay visible in the output. |
| `free_llm_recipe` | Run a bundled recipe end-to-end (e.g. `pr-review`, `second-opinion`, `repo-summary`). Bounded, role-driven, and pre-shaped; missing input/variables return a tool error, not a traceback. Args: `name`, `prompt`, `path`, `input`, `validation_output`, `opinions`, `synthesize`, `max_tokens`. |
| `free_llm_roles` | List bundled ask-role presets (`coder`, `critic`, `summarizer`, `second-opinion`, …) with routing, max-tokens, and recommended use. Pass `name` for one role. |
| `free_llm_tailnet_info` | Show safe Tailscale Tailnet connection instructions for serving the proxy on another machine. Output NEVER contains a real local bearer token (uses a `<proxy-key>` placeholder) and never leaks provider API keys. Degrades cleanly when `tailscale` is absent. Optional `port` (default 8080). |
| `free_llm_quota_wise` | Local quota-mode / headroom advice from local counters only. Output NEVER recommends account rotation, rate-limit bypass, or automatic paid fallback — only waiting for the local counter's UTC rollover or the upstream provider's own reset window, lowering fan-out/token budget, or making an explicit paid choice outside the default flow. |
| `tokenmax` | 🌈 Gloriously excessive: fan the prompt out to automatically eligible ranked targets across configured providers (hard cap 256; a max, routing policy, or CLI `wise` mode may narrow the swarm), then the **calling** model synthesizes the returned responses. Emits live `notifications/progress` (`🌈 TOKENMAXXING ▸ N/total models…`) so hosts like Claude Code show it ticking up, and a colorful rainbow banner in the result. Tongue-in-cheek, genuinely useful for hard questions. |
| `free_llm_route` | Explain where a prompt **would** route (estimated difficulty, resolved task, and ranked candidate models/evidence) **without spending a token**. |
| `free_llm_models` | List available `provider/model` ids. |
| `free_llm_quota` | Today's per-provider usage + daily-limit headroom, plus session totals and estimated cost avoided. |
| `free_llm_stats` | Lifetime tokens served free + estimated cost avoided vs Claude Opus 4.8 (persists across restarts). |

## Claude Desktop

Edit `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "freellmpool": {
      "command": "freellmpool",
      "args": ["mcp"]
    }
  }
}
```

Restart Claude Desktop. Ask it to *"use free_llm_ask to summarize this"* and it
will route to a free model.

## Claude Code

```bash
claude mcp add freellmpool -- freellmpool mcp
```

## Cursor

`~/.cursor/mcp.json` (or Settings → MCP):

```json
{
  "mcpServers": {
    "freellmpool": { "command": "freellmpool", "args": ["mcp"] }
  }
}
```

## Adding provider keys

Pass them through the MCP server's environment, e.g. in the config:

```json
{
  "mcpServers": {
    "freellmpool": {
      "command": "freellmpool",
      "args": ["mcp"],
      "env": { "GROQ_API_KEY": "gsk_...", "CEREBRAS_API_KEY": "csk-..." }
    }
  }
}
```

## Notes

- The server speaks newline-delimited JSON-RPC 2.0 over stdio (the standard MCP
  stdio transport) and is implemented on the Python standard library only.
- `stdout` carries the protocol; freellmpool prints its banner to `stderr`.
- Tools are meant to be invoked through the MCP client. Shelling out to
  `freellmpool mcp` from inside an agent hides progress notifications and can
  break the stdio protocol.
- **Invoke the tools directly.** The `initialize` handshake returns an
  `instructions` field telling the calling agent to call these as MCP tools — **not**
  to shell out to the `freellmpool` CLI as a subprocess. Subprocessing captures the
  output inside the agent's process and hides `tokenmax`'s live progress, the rainbow
  banner, and the answers from the user.
- **Want the *visible* rainbow animation?** An MCP tool can't paint a live animation in
  the host chat (stdout is the protocol, stderr is logged, results strip ANSI) — the
  `tokenmax` `notifications/progress` status line is the ceiling here. For a genuine
  in-harness graphic, use the **OpenCode embedded TUI plugin** (`integrations/opencode-tui`),
  whose panel throbs a live rainbow `TOKENMAXXING` animation; or run `freellmpool tokenmax`
  in a real terminal for the standalone flash.
- **Live `tokenmax` progress:** when a client passes a `progressToken` (Claude
  Code does), `tokenmax` streams `notifications/progress` as each selected model answers, so
  you see the swarm tick up in real time. Raw ANSI can't animate inside an MCP
  host's chat, so progress + a rainbow emoji banner are how the spectacle "comes
  through" there. For the **genuine** flashing rainbow animation, run it in a real
  terminal: `freellmpool tokenmax "your question"` (it also prints every answer and
  a synthesized verdict).
