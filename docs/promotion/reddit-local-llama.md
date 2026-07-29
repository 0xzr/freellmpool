# Reddit / r/LocalLLaMA draft

Target: `r/LocalLLaMA`.

Why this belongs there: many LocalLLaMA users run local models first but still
use hosted free tiers for overflow, comparison, or coding-agent side tasks. The
post must be explicit that freellmpool is local software over hosted upstream
providers, not local/offline inference.

Rules to respect: disclose affiliation, keep self-promotion rare, and avoid
"I found this" framing. If your recent Reddit history is mostly project
promotion, wait and contribute elsewhere first.

## Title options

- freellmpool: a local router for hosted free LLM API tiers behind one OpenAI-compatible endpoint
- freellmpool: one local proxy over Groq, Cerebras, Gemini, Mistral, OpenRouter, NVIDIA, and other free tiers
- Pool free hosted LLM tiers for coding agents and scripts with one local proxy

## Body

I built `freellmpool`, an MIT-licensed Python tool for pooling legitimate free
LLM API tiers behind one local interface.

Repo: https://github.com/0xzr/freellmpool

Up front: these are hosted providers' free tiers, not local/offline models. The
router runs locally, but prompts go to the selected upstream provider. If you
only run local models, this is a complement rather than a replacement.

Why I wanted this:

- every provider has a different key, SDK, model list, rate limit, and failure
  mode;
- several free tiers are useful but individually small;
- coding agents and scripts usually just want one OpenAI-compatible base URL;
- free provider catalogs drift constantly.

Where it fits next to local models:

- keep local models as the default for private/offline work;
- use hosted free tiers when the local GPU is busy;
- run second-opinion panels across small hosted models before spending paid
  quota;
- give coding agents a fallback path for docs, summaries, triage, and small
  edits;
- test whether a hosted model is worth adding to your regular stack.

What it does:

- `freellmpool ask "..."` for one-shot CLI calls;
- local OpenAI-compatible proxy for existing clients;
- experimental Anthropic-compatible proxy path for coding agents;
- MCP server for Claude Desktop/Code/Cursor;
- provider failover on 429s, timeouts, server errors, empty replies, and stale
  routes;
- local quota tracking so traffic can be spread across free tiers;
- `tokenmax` mode that fans out to many available models and returns their
  answers side-by-side for comparison.

Current catalog:

- 24 cataloged providers
- 222 enabled chat routes
- 407 cataloged chat models
- keyless start when default keyless routes are available, more capacity when
  you add your own free keys

Example:

```bash
pip install freellmpool
freellmpool ask "Explain KV cache in one paragraph."

freellmpool proxy
export OPENAI_BASE_URL=http://localhost:8080/v1
export OPENAI_API_KEY=anything
```

It is not a privacy layer. I wrote the FAQ to make the provider routing and
prompt-destination tradeoffs explicit:

https://github.com/0xzr/freellmpool/blob/main/FAQ.md

I would like feedback from this community on provider quality, missing free-tier
providers, and whether the routing behavior matches how people actually use
hosted free models alongside local models.
