# Exhaustive model activity audit — 2026-07-17

This audit sent `Reply with the single word: pong` through the real
`freellmpool.client.call` path for every chat route that could be authenticated,
including disabled routes. It also exercised every embedding and transcription
route with real payloads. No repository content or user prompt was sent.

## Coverage and method

- **381 of 397 chat routes received live completion probes.** This covers every
  chat route on the 18 locally configured providers, plus three newly discovered
  routes tested separately three times each.
- The remaining 16 chat routes belong to Gemini, Aion Labs, ModelScope, and
  SiliconFlow. No usable local credentials were available. Aion's public model
  list contained all five exact catalog IDs, and ModelScope's contained all
  seven; SiliconFlow requires authentication for model discovery.
- Unauthenticated completion probes reached all four API boundaries: Aion,
  ModelScope, and SiliconFlow returned 401, while Gemini returned 400 for its
  missing API credential. No route was falsely counted as a live completion.
- Transient HTTP statuses and transport failures received up to three attempts
  with a 60-second request ceiling. Definitive 400/404/410 responses were
  repeated before catalog changes. Slow NVIDIA routes also received a final
  serial pass with no competing provider calls.
- **All 26 embedding and 5 transcription routes were exercised.** After fixing
  NVIDIA's required `input_type` request field, every enabled capability route
  passed: 21 embeddings and 5 transcription routes. Five retired NVIDIA
  embedding routes returned repeat 404s and are now disabled.

## Current enabled-route result

The resulting catalog has **22 providers, 239 enabled chat routes, and 397
cataloged chat models**.

| Result for the 239 currently enabled chat routes | Routes |
|---|---:|
| Non-empty live completion | 196 |
| Hugging Face account exhausted its included monthly credits (HTTP 402) | 17 |
| Provider rate-limited during the audit (HTTP 429) | 10 |
| Not completion-tested because no credential was available | 16 |

No route left enabled returned a definitive missing, retired, empty, degraded,
or repeat-timeout result.

## Catalog decisions

### Enabled or added after repeat success

- LLM7 `minimax-m2.7` passed three times and was re-enabled.
- Kilo `kwaipilot/kat-coder-pro-v2.5:free` passed three times and was added.
- GitHub Models `openai/gpt-4.1-mini` passed three times with the active GitHub
  CLI credential and was re-enabled.
- NVIDIA `bytedance/seed-oss-36b-instruct`, `minimaxai/minimax-m2.7`,
  `qwen/qwen3-next-80b-a3b-instruct`, and `qwen/qwen3.5-122b-a10b` passed three
  total probes each and were re-enabled.
- NVIDIA `poolside/laguna-xs-2.1` and `thinkingmachines/inkling` each passed
  three times and were added.

### Disabled after repeat definitive failure

- Ollama explicitly retired 12 enabled routes on 2026-07-15: `qwen3-coder:480b`,
  `ministral-3:3b`, `gemma3:4b`, `gemma3:27b`, `qwen3-coder-next`,
  `minimax-m2.1`, `devstral-2:123b`, `devstral-small-2:24b`, `gemma3:12b`,
  `glm-4.7`, `ministral-3:8b`, and `ministral-3:14b`. The listed `glm-5.2` and
  `kimi-k2.7-code` replacements both required a paid subscription and were not
  added.
- GitHub Models returned repeat `Unknown model` responses for four Llama routes:
  the 11B and 90B Llama 3.2 vision models and the 405B and 8B Meta-Llama 3.1
  models.
- NVIDIA returned repeat 410s for `microsoft/phi-4-mini-instruct` and
  `stockmark/stockmark-2-100b-instruct`. Four routes timed out throughout both
  the concurrent and isolated passes, and `mistralai/mixtral-8x7b-instruct-v0.1`
  reported a degraded, non-invocable backend; all seven are now disabled from
  automatic routing.
- Five NVIDIA embedding IDs returned repeat 404s and are disabled. The router
  now honors disabled flags for embedding and transcription models.

## Other provider findings

- Every enabled Pollinations, LLM7, OVHcloud, Kilo, Groq, Cerebras, Cloudflare,
  Mistral, Cohere, Z.ai, and non-retired Ollama route returned a non-empty
  completion.
- OpenRouter had 13 successes and seven 429-limited enabled routes. Rate limits
  are transient and did not change catalog state.
- OpenCode's five free routes all answered but remain disabled by the existing
  privacy/retention opt-in policy.
- Hugging Face returned five successes before reporting the same provider-wide
  depleted-credit response for 17 enabled routes. This is account/quota state,
  not evidence that those model IDs are dead.
- LongCat's listed successor returned repeat HTTP 402 quota/payment responses.
  SambaNova's catalog remains disabled because its routes are payment-gated.

Raw reports remain under `/tmp/flp_vet_20260717`, with targeted repeat reports
under `/tmp/flp_*_20260717.json`. They are not committed because provider error
payloads may contain account-specific diagnostic identifiers.
