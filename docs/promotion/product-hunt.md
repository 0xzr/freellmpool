# Product Hunt draft

Product Hunt is optional. Use it only if you have time to stay active in comments
for the launch day.

## Name

freellmpool

## Tagline

Pool free LLM tiers behind one local API

## Short description

freellmpool is an open-source Python tool that pools legitimate free LLM provider
tiers behind one CLI, local OpenAI-compatible proxy, Python library, and MCP
server. It supports keyless start when default keyless routes are available and
adding your own free-tier keys for more capacity.

## Maker comment

I built freellmpool because free LLM tiers are useful, but fragmented. Each
provider has its own keys, model ids, limits, SDK shape, and failure modes.

freellmpool makes those tiers usable from one local interface:

- `freellmpool ask` for one-shot CLI use
- local OpenAI-compatible proxy
- experimental Anthropic-compatible proxy path
- MCP server
- provider failover and local quota tracking
- keyless start when default keyless routes are available

Current catalog: 24 cataloged providers, 223 enabled chat routes, and 407 cataloged chat
models.

This is not meant to replace paid frontier models or bypass provider limits. It
is a local open-source router for legitimate free tiers, useful for docs, triage,
classification, everyday coding support, and agent side tasks.

GitHub: https://github.com/0xzr/freellmpool

## Gallery asset ideas

- `assets/social-preview.png`
- `assets/demo.png`
- `assets/tokenmax-results.png`
- screenshot of `freellmpool providers`
- screenshot of `freellmpool tokenmax`
- diagram: CLI / proxy / MCP -> router -> provider free tiers

## Likely questions

### Is this free?

The package is MIT-licensed. Provider free tiers are controlled by their
providers and may change. Several default routes can start without a key; adding
your own free-tier keys increases capacity.

### Where do prompts go?

Directly to the selected provider/model. freellmpool is local software and not a
hosted relay. See the FAQ for a provider-by-provider table:
https://github.com/0xzr/freellmpool/blob/main/FAQ.md

### How is this different from OpenRouter or LiteLLM?

OpenRouter is a polished hosted aggregator. LiteLLM is the mature
bring-your-own-keys proxy/SDK. freellmpool is narrower: it is local,
pip-installable, starts without a key, focuses on legitimate free tiers, and
ships a CLI plus MCP tools.
