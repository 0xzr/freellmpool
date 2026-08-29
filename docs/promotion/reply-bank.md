# Reply bank

Use these as starting points for HN, Reddit, GitHub Discussions, and social
comments.

## Where do prompts go?

Prompts go directly to the selected upstream provider/model. `freellmpool` is a
local router, not a hosted relay and not a privacy layer. The FAQ has a
provider-by-provider table:

https://github.com/0xzr/freellmpool/blob/main/FAQ.md

## Is this evading rate limits?

No. The tool is meant to use legitimate provider free tiers. It tracks local
usage and fails over when a provider is rate-limited, unavailable, or returning
bad responses. It does not bypass provider policies.

## Why not just use OpenRouter?

OpenRouter is a hosted aggregator and is very polished. `freellmpool` is local,
open source, and pip-installable; it can start without a key while an enabled
keyless route is available, and exposes accessible provider routes through a
CLI, library, proxy, and MCP server. It can also use OpenRouter as one provider.

## Why not LiteLLM?

LiteLLM is the mature bring-your-own-keys proxy/SDK. `freellmpool` is narrower:
it focuses on pooling legitimate free tiers, first-run keyless usage, a simple
CLI, and the free-tier catalog.

## Why not Ollama, LM Studio, or LocalAI?

Those are for running or serving models locally. `freellmpool` is local software
that routes to hosted upstream providers' free tiers. Use local models for
private/offline work; use freellmpool when you want a convenient fallback,
second opinion, or OpenAI-compatible proxy over hosted free routes.

## Why not FreeLLMAPI?

FreeLLMAPI predates this project. The overlap is independent convergence:
free-tier pooling is a useful idea. `freellmpool` focuses on pip-installable
local usage, a one-shot CLI, MCP tools, and keyless start when default routes
are available.

## Is it really free?

The software is MIT-licensed. Provider free tiers are provider-owned and can
change. Some need accounts or keys; several default routes can start without a
key. Add your own free-tier keys for more capacity.

## Can this replace paid frontier models?

No. It is useful for drafting, classification, docs, everyday coding support,
triage, and side tasks. Hard reasoning still benefits from frontier models.

## Does it support coding agents?

Yes, through the local proxy. It speaks the OpenAI API and has an experimental
Anthropic-compatible path. There are recipes for Codex, Claude Code, aider,
Cline, Continue, Cursor, and OpenCode.

## How do I use it with OpenCode?

Start the proxy:

```bash
freellmpool proxy --port 8080
```

Then add a custom OpenAI-compatible provider in `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "freellmpool/auto",
  "provider": {
    "freellmpool": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://localhost:8080/v1",
        "apiKey": "{env:FREELLMPOOL_PROXY_KEY}"
      },
      "models": { "auto": {}, "fast": {}, "quality": {}, "fair": {} }
    }
  }
}
```

This is for the OpenCode app/CLI. OpenCode Zen routes are cataloged separately
and disabled by default pending opt-in and provider-policy review.

## How do I use it with Claude Code, Cursor, or other MCP clients?

Use the local stdio MCP server. It is started by the MCP host; it is not a
hosted/remote MCP service:

```bash
claude mcp add freellmpool -- freellmpool mcp
```

For JSON MCP configs:

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

The useful tools are `free_llm_ask`, `free_llm_panel`, `tokenmax`,
`free_llm_route`, `free_llm_models`, `free_llm_quota`, and `free_llm_stats`.

## How do I use it with metaswarm?

Use the review-only adapter in `integrations/metaswarm`. It is meant for review
and second opinions, not implementation:

```bash
mkdir -p .metaswarm/adapters
cp integrations/metaswarm/freellmpool-review-adapter.sh .metaswarm/adapters/freellmpool.sh
chmod +x .metaswarm/adapters/freellmpool.sh
```

Then add the adapter to `.metaswarm/external-tools.yaml` with roles
`["review", "second_opinion"]`. Configure at least one strong review provider
key first; otherwise it fails closed with `error_type: "auth_missing"`. That
stops the review call rather than silently substituting a different provider.

## What captions should I use for the images?

- `assets/demo.png`: "Terminal demo showing freellmpool routing through its
  local proxy and reporting catalog/provider status." Alt text: "Screenshot of a
  terminal running freellmpool with proxy, provider catalog, and routing output."
- `assets/tokenmax-results.png`: "tokenmax summary card showing enabled routes,
  cataloged providers, keyless start, and model fan-out behavior." Alt text:
  "Social card for freellmpool tokenmax with stats for enabled routes, cataloged
  providers, and keyless start."
- `assets/social-preview.png`: "Project preview card for freellmpool: free LLM
  API pool for agents and local proxies." Alt text: "Dark social preview image
  for freellmpool with feature labels for keyless start, 22 providers, OpenAI
  proxy, MCP, transcription, and tokenmax."

## Can I use it with Cline or Cursor's OpenAI-compatible settings?

Yes. Start `freellmpool proxy --port 8080`, set the base URL to
`http://localhost:8080/v1`, set the API key to any placeholder value, and use
model `auto`. Treat it as a free-tier fallback for small tasks, not as a
replacement for strong paid coding models.

## What should contributors help with?

Provider drift reports are the most valuable. Free model ids and limits change
often. The repo also has scoped good-first issues:

https://github.com/0xzr/freellmpool/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22good%20first%20issue%22

## What should I not send through it?

Do not send secrets or confidential data unless you are comfortable with the
selected provider's current terms, privacy posture, and retention behavior.
Anonymous keyless routes are convenient, not private.

## Why is OpenCode Zen cataloged but disabled by default?

Some routes are useful to track but not reliable or reviewed enough for default
routing. OpenCode Zen is cataloged as a keyless OpenAI-compatible endpoint, but
its free routes ship disabled by default pending explicit opt-in and provider
policy review.
