# Model activity audit — 2026-08-23

This maintenance audit records the public evidence used for the Pollinations
catalog refresh. No credentials, prompts beyond the fixed canary, or response
content are stored here.

## Pollinations

- Authoritative listing: <https://text.pollinations.ai/models>
- Canonical listed route: `openai-fast`
- Listed aliases retained as exact pins: `openai`, `gpt-oss`
- Retired catalog row: `mistral` (absent from the authoritative listing)

The `gpt-oss` alias was exercised through the packaged OpenAI-compatible client
with the fixed single-word canary. The provider resolved it to `gpt-oss-20b`.

| Selector | Attempts | HTTP 200 | Non-empty | Decision |
| --- | ---: | ---: | ---: | --- |
| `gpt-oss` | 3 | 3 | 3 | Retain as an exact pin; exclude alias from automatic fan-out |

The canonical `openai-fast` route remains the single automatic Pollinations
target so failover and tokenmax do not call the same backend through aliases.
