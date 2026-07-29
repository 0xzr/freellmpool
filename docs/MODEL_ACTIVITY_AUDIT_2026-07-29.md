# Exhaustive model activity audit — 2026-07-29

This audit sent only fixed synthetic canaries through freellmpool's real client
paths. Chat used `Reply with the single word: pong`, embeddings used
`fixed catalog audit canary`, and transcription used a generated 0.25-second
440 Hz WAV. No repository content, user prompt, credential value, or
account-specific diagnostic identifier is retained here.

Raw reports remain under `/tmp` because upstream diagnostics can contain
account-specific identifiers. This document contains only sanitized aggregates
and catalog decisions.

## Coverage and method

- **383 of 407 chat routes were live-probed**, including enabled and disabled
  routes on all 18 configured providers.
- The 24 routes without a usable maintenance configuration are provider-specific:
  Aion Labs (5; no `AION_API_KEY`), ModelScope (7; no
  `MODELSCOPE_API_KEY`), Morph (3; no `MORPH_API_KEY`), Vercel AI Gateway
  (5; its billing-safety opt-in was deliberately absent), SiliconFlow (1; no
  `SILICONFLOW_API_KEY`), and Gemini (3 cataloged, including two disabled
  lifecycle rows; no `GEMINI_API_KEY`).
- Transient HTTP statuses and transport failures received up to three attempts.
  Every enabled route classified as definitively unavailable was then called
  three more times independently. All 25 repeated the same definitive
  400/404/410 classification before being disabled.
- A 401, 402, 429, timeout, provider-wide quota error, or isolated 5xx did not
  disable any route.
- All **21 enabled embedding routes** and **5 enabled transcription routes**
  were exercised with real payloads. Seventeen embedding and all five
  transcription routes passed. Cloudflare returned 429 for one embedding,
  GitHub returned 401 for two embeddings with the locally unavailable
  credential, and NVIDIA returned one isolated 500; these remain enabled
  because none is retirement evidence.

## Current enabled-route result

The resulting catalog has **24 providers, 222 enabled chat routes, and 407
cataloged chat models**.

| Result for the 222 currently enabled chat routes | Routes |
|---|---:|
| Non-empty live completion | 126 |
| Provider rate-limited during the audit (HTTP 429) | 31 |
| GitHub maintenance credential rejected (HTTP 401) | 20 |
| Hugging Face account exhausted included credits (HTTP 402) | 21 |
| Isolated NVIDIA read timeout | 2 |
| Not completion-tested because no safe usable configuration was available | 22 |

The non-success classifications above describe account or transient state, not
model retirement, and therefore did not change catalog state.

## Disabled after repeat definitive failure

Each route below failed the full sweep and three independent repeat canaries
with the same definitive status:

- Groq (404): `meta-llama/llama-4-scout-17b-16e-instruct`,
  `qwen/qwen3-32b`.
- Hugging Face (400 unsupported): `CohereLabs/command-a-vision-07-2025`.
- Kilo Gateway (404 unavailable/free period ended):
  `kwaipilot/kat-coder-pro-v2.5:free`, `poolside/laguna-m.1:free`,
  `tencent/hy3:free`.
- LLM7 (400 unavailable): `minimax-m2.7`.
- NVIDIA (410 Gone): `abacusai/dracarys-llama-3.1-70b-instruct`,
  `bytedance/seed-oss-36b-instruct`, `minimaxai/minimax-m2.7`,
  `mistralai/mistral-large-3-675b-instruct-2512`,
  `mistralai/mistral-small-4-119b-2603`,
  `qwen/qwen3-next-80b-a3b-instruct`, `qwen/qwen3.5-122b-a10b`,
  `sarvamai/sarvam-m`, `stepfun-ai/step-3.5-flash`, and
  `upstage/solar-10.7b-instruct`.
- OpenRouter (404 no free endpoint):
  `cognitivecomputations/dolphin-mistral-24b-venice-edition:free`,
  `meta-llama/llama-3.2-3b-instruct:free`,
  `meta-llama/llama-3.3-70b-instruct:free`,
  `nousresearch/hermes-3-llama-3.1-405b:free`,
  `poolside/laguna-m.1:free`, `qwen/qwen3-coder:free`,
  `qwen/qwen3-next-80b-a3b-instruct:free`, and `tencent/hy3:free`.

The rows remain cataloged as disabled lifecycle history and can still be
explicitly pinned. They cannot enter automatic routing.

## Gemini lifecycle

Google's official deprecation table and June 1 release note mark
`gemini-2.0-flash` and `gemini-2.0-flash-lite` as shut down on June 1, 2026.
Both remain cataloged as disabled lifecycle history.

The maintenance environment had no `GEMINI_API_KEY`, so no Gemini 3 route was
enabled from documentation alone. `gemini-2.5-flash` remains enabled while
Google lists its shutdown no earlier than October 16, 2026. A replacement must
pass repeat non-empty calls on a real free-tier project before enablement.

Official sources:

- <https://ai.google.dev/gemini-api/docs/deprecations>
- <https://ai.google.dev/gemini-api/docs/changelog>
- <https://ai.google.dev/gemini-api/docs/models>

## LLM7 durable free selectors

LLM7's official documentation recommends `default` and `fast`, states that
concrete model IDs are being phased out, and reserves `pro` for paid plans.
Anonymous/keyless access is documented as free but rate-limited.

| Selector | Successful HTTP 200 responses | Non-empty completions |
|---|---:|---:|
| `default` | 3/3 | 3/3 |
| `fast` | 3/3 | 3/3 |

The `fast` canary used a 128-token output budget because its selected reasoning
model consumed a smaller budget before emitting visible content. The catalog
adds `default` and `fast` with conservative request hints and deliberately omits
the paid `pro` selector.

Official sources:

- <https://docs.llm7.io/guides/models>
- <https://docs.llm7.io/quickstart>
- <https://docs.llm7.io/limits>

## Advisory discoveries and recoveries

Discovery reported many catalog gaps. They were not added because listing
presence is not proof of durable free completion access. Four disabled OpenCode
rows and one disabled NVIDIA row answered once, but one success does not meet
the three-success re-enable rule. OpenCode also remains subject to its existing
privacy/retention opt-in policy. No route was auto-enabled from discovery.
