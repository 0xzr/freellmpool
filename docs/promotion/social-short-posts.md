# Short social posts

## X / Bluesky short

I built freellmpool: a local, open-source pool for free LLM API tiers.

- 22 cataloged providers
- 239 enabled chat routes
- OpenAI-compatible local proxy
- MCP server
- keyless start when default keyless routes are available
- add your own free keys for more capacity

https://github.com/0xzr/freellmpool

## X / Bluesky thread

1/ I built `freellmpool`, a local open-source gateway that pools free LLM API
tiers behind one interface.

Repo: https://github.com/0xzr/freellmpool

2/ Why: every provider has a different key, model list, SDK, quota, and failure
mode. Individually, free tiers are small. Together, they are useful for scripts,
coding agents, docs, triage, and side tasks.

3/ It ships as:

- CLI
- Python library
- local OpenAI-compatible proxy
- experimental Anthropic-compatible proxy path
- MCP server

4/ Current catalog:

- 22 cataloged providers
- 239 enabled chat routes
- 397 cataloged chat models
- keyless start when default keyless routes are available

5/ It handles practical routing problems: 429s, timeouts, empty replies, stale
model ids, and local per-day quota tracking.

6/ It is not magic and not a privacy layer. Prompts go to the selected provider,
and free-tier model quality varies. I wrote the FAQ to make the tradeoffs clear:

https://github.com/0xzr/freellmpool/blob/main/FAQ.md

7/ If this is useful, a star helps other developers find it. Provider drift
reports and small catalog fixes are especially welcome.

https://github.com/0xzr/freellmpool

## X / Bluesky OpenCode-specific

I wired OpenCode to `freellmpool` as a local OpenAI-compatible provider:

- `freellmpool proxy --port 8080`
- model picker: `freellmpool/auto|fast|quality|fair`
- routes across legitimate hosted free tiers
- optional OpenCode TUI/status plugins

Repo/config:
https://github.com/0xzr/freellmpool

## X / Bluesky MCP-specific

`freellmpool mcp` lets Claude Code, Claude Desktop, Cursor, and other MCP
clients hand off small tasks to free LLM tiers:

- one-shot asks
- multi-model panels
- tokenmax fan-out
- route previews
- quota/status tools

https://github.com/0xzr/freellmpool

## X / Bluesky metaswarm-specific

I added a review-only metaswarm adapter for `freellmpool`.

Coding agents still implement. freellmpool supplies cheap independent review and
second-opinion panels across free-tier providers, with fail-closed auth checks
for strong review routes.

https://github.com/0xzr/freellmpool

## LinkedIn

I released `freellmpool`, an MIT-licensed Python tool for pooling free LLM API
tiers behind one local interface.

The practical problem: free LLM tiers are useful, but each provider has its own
keys, model ids, quotas, SDKs, and failure modes. `freellmpool` gives scripts,
tools, and coding agents one local gateway with failover and quota tracking.

It supports:

- CLI and Python library usage
- a local OpenAI-compatible proxy
- an experimental Anthropic-compatible proxy path
- MCP server tools
- keyless start when default keyless routes are available, with optional
  free-tier provider keys for more capacity

Current catalog: 22 cataloged providers, 239 enabled chat routes, and 397 cataloged chat
models.

It is not a privacy layer. Prompts go to the selected upstream provider; the FAQ
covers that tradeoff:
https://github.com/0xzr/freellmpool/blob/main/FAQ.md

This is not meant to replace frontier paid models. It is useful for everyday
developer tasks where free-tier models are good enough: docs, triage,
classification, drafting, and agent side work.

Repo: https://github.com/0xzr/freellmpool

## Mastodon

I built `freellmpool`, a local open-source gateway for pooling free LLM provider
tiers.

CLI, Python library, local OpenAI-compatible proxy, experimental
Anthropic-compatible path, and MCP server.

22 cataloged providers, 239 enabled chat routes, keyless start when routes are available.

https://github.com/0xzr/freellmpool
