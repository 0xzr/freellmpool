# Hacker News draft

## Title

Show HN: freellmpool - pool free LLM tiers behind one local OpenAI-compatible API

## Post body

Hi HN,

I built `freellmpool`, a small MIT-licensed Python tool that pools the free tiers
of multiple LLM providers behind one local interface.

GitHub: https://github.com/0xzr/freellmpool

It can run as:

- a one-shot CLI: `freellmpool ask "..."`;
- a Python library;
- a local OpenAI-compatible proxy;
- an experimental Anthropic-compatible proxy path for coding agents;
- an MCP server with tools like `free_llm_ask`, `free_llm_panel`, and `tokenmax`.

The current catalog has 24 cataloged providers, 247 enabled chat routes, and 405 cataloged
chat models. Some routes work without any API key, so the quickstart can produce
a first reply after `pip install freellmpool`; adding your own free-tier keys for
Groq, Cerebras, Gemini, Mistral, OpenRouter, NVIDIA, Cohere, and others expands
capacity and failover.

The main thing it does is fail over and spread load across provider free tiers.
If a provider returns a 429, times out, gives an empty reply, or has a stale model
id, the router can try the next eligible provider. It also tracks local per-day
usage so one free tier does not get drained immediately.

I tried to be explicit about the parts people usually ask about:

- Where prompts go: https://github.com/0xzr/freellmpool/blob/main/FAQ.md
- Provider catalog: https://github.com/0xzr/freellmpool/blob/main/src/freellmpool/providers.toml
- MCP docs: https://github.com/0xzr/freellmpool/blob/main/docs/MCP.md

This is not meant to replace paid frontier models. It is useful for drafting,
classification, everyday coding support, triage, docs, and agent side tasks where
free-tier models are good enough.

I would especially appreciate feedback on:

- provider rows that have drifted;
- free-tier providers I missed;
- whether the proxy/MCP interfaces fit how people actually use local tools;
- whether the privacy/ToS FAQ is clear enough.

## First comment

A couple of caveats up front:

- Prompts go directly to whichever provider/model is selected. This is a local
  router, not a privacy layer.
- The tool respects provider free tiers and rate limits. It is not trying to
  evade limits.
- Free model availability drifts quickly. The catalog includes disabled rows for
  routes that exist but are not reliable enough for default routing.
- The Claude Code / Anthropic-compatible path is experimental.

The project has focused newcomer issues here:
https://github.com/0xzr/freellmpool/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22good%20first%20issue%22
