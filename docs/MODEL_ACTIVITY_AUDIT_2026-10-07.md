# Model activity audit — 2026-10-07

This maintenance audit records the evidence for the 2026-10-07 catalog
refresh. It is the first refresh run with the local procedure in
[`CATALOG_SENTINEL.md`](CATALOG_SENTINEL.md): completion probes ran on a
maintainer machine with its local freellmpool key configuration, and no
provider key was stored in GitHub. No credentials, account identifiers,
prompts beyond the fixed canary, or response content are stored here.

## Method

1. Keyless public discovery: `scripts/catalog_sentinel.py discover` for all 23
   provider groups.
2. Pass 1: `scripts/vet_catalog.py` pinged all 384 cataloged chat routes of the
   16 locally configured providers (pollinations, llm7, ovh, kilo, opencode,
   huggingface, groq, cerebras, nvidia, openrouter, cloudflare, mistral, cohere,
   sambanova, zhipu, ollama) through the packaged client with the fixed
   single-word canary, retrying transient statuses.
3. Pass 2 (and pass 3 for one route): candidate changes were probed again
   through the packaged client: one more call for each suspected retirement,
   and three fresh sequential calls for each route considered for enabling or
   adding.

Vercel was left out because its priced routes spend gateway credit. Requesty,
Gemini, Aion Labs, ModelScope, Morph, and SiliconFlow had no local credential,
so they received public discovery only and their routes are unchanged.

The 2026-08-29 evidence rules still apply: a 401, 402, 429, timeout,
provider-wide quota result, or isolated 5xx is not retirement evidence. A route
was disabled only when both passes returned the same definitive model-level
failure (404, 410, invalid model) or a paywall or sign-in gate on a route the
catalog advertised as free. A route was enabled or added only after three
fresh sequential non-empty completions. It joins automatic routing only when
every one of those canaries finished within 15 seconds and the provider has no
single-automatic-selector policy; otherwise it is enabled exact-pin-only.

## Counts

| Modality | Provider groups | Cataloged | Enabled | Automatic | Enabled exact-pin-only |
|---|---:|---:|---:|---:|---:|
| Chat, before | 23 | 438 | 183 | 154 | 29 |
| Chat, after | 23 | 443 | 178 | 147 | 31 |

Embeddings (25 cataloged / 14 enabled) and transcription (5 / 5) were not
probed and are unchanged, so enabled capability routes go from 202 to 197.

## Disabled (17)

Each route failed the same way in both passes. The Mistral Large routes return
`tier_not_allowed` on the free tier, Ollama's `minimax-m3` says it is not
included in free usage, and Kilo's keyless `longcat-2.0-free` now requires
sign-in.

| Provider | Route | Pass 1 | Pass 2 |
| --- | --- | --- | --- |
| `cerebras` | `gemma-4-31b` | 404 not found | 404 not found |
| `groq` | `groq/compound` | 404 not found | 404 not found |
| `groq` | `groq/compound-mini` | 404 not found | 404 not found |
| `groq` | `qwen/qwen3.6-27b` | 404 not found | 404 not found |
| `kilo` | `inclusionai/ling-3.0-flash-fin:free` | 404 not found | 404 not found |
| `kilo` | `meituan/longcat-2.0-free` | 401 sign-in required | 401 sign-in required |
| `kilo` | `minimax/minimax-m2.7:free` | 404 not found | 404 not found |
| `kilo` | `tencent/hy3:free` | 404 not found | 404 not found |
| `mistral` | `magistral-medium-2509` | 400 invalid model | 400 invalid model |
| `mistral` | `magistral-small-2509` | 400 invalid model | 400 invalid model |
| `mistral` | `mistral-large-2512` | 403 not in subscription tier | 403 not in subscription tier |
| `mistral` | `mistral-large-latest` | 403 not in subscription tier | 403 not in subscription tier |
| `nvidia` | `minimaxai/minimax-m3` | 410 gone | 410 gone |
| `nvidia` | `mistralai/mistral-nemotron` | 410 gone | 410 gone |
| `nvidia` | `nvidia/nemotron-3-nano-30b-a3b` | 410 gone | 410 gone |
| `ollama` | `minimax-m3` | 402 not included in free usage | 402 not included in free usage |
| `ovh` | `Qwen3-32B` | 404 not found | 404 not found |

## Re-enabled (7) and added (5)

The NVIDIA Kimi K3 and Nemotron 3.5 Lightning routes and the two OpenRouter
routes were 2026-08-29 listing-only candidates pending credentialed canaries.
`gpt-oss:20b` (LLM7), `ovh-reasoning` (Pollinations), and NVIDIA
`google/gemma-4-31b-it` were disabled after failed canaries. LLM7 and
Pollinations keep their existing automatic selectors, so their recovered
routes are pin-only. OpenRouter `dots-studio/dots-3-note-preview:free` went
2/3 in pass 2 (one 502) and 3/3 in pass 3; the table shows pass 3.

| Provider | Route | Change | Fresh canaries | Slowest | Routing |
| --- | --- | --- | ---: | ---: | --- |
| `llm7` | `gpt-oss:20b` | re-enabled | 3/3 | 0.5 s | pin-only |
| `pollinations` | `ovh-reasoning` | re-enabled | 3/3 | 0.4 s | pin-only |
| `nvidia` | `google/gemma-4-31b-it` | re-enabled | 3/3 | 21.0 s | pin-only |
| `nvidia` | `moonshotai/kimi-k3` | re-enabled | 3/3 | 26.7 s | pin-only |
| `nvidia` | `nvidia/nemotron-3.5-lightning-30b-a3b` | re-enabled | 3/3 | 5.1 s | automatic |
| `openrouter` | `dots-studio/dots-3-note-preview:free` | re-enabled | 3/3 | 4.0 s | automatic |
| `openrouter` | `nvidia/nemotron-3.5-lightning:free` | re-enabled | 3/3 | 5.6 s | automatic |
| `kilo` | `inclusionai/ling-3.0-flash-sante:free` | added | 3/3 | 1.6 s | automatic |
| `nvidia` | `z-ai/glm-5.3` | added | 3/3 | 14.2 s | automatic |
| `openrouter` | `apodex/apodex-1.1-mini:free` | added | 3/3 | 1.8 s | automatic |
| `openrouter` | `inclusionai/ling-3.0-flash-sante:free` | added | 3/3 | 0.9 s | automatic |
| `ovh` | `Qwen3.8-27B` | added | 3/3 | 54.3 s | pin-only |

## Not changed

- Account state, not route evidence: Hugging Face returned 402 "no remaining
  credits" for all 21 enabled routes, Cohere's trial key returned 429 for all
  16 (monthly cap), Mistral returned 429 "Rate limit exceeded" for 17 enabled
  Small, Medium, Devstral, Magistral, Code, and Vibe routes, Cerebras `gpt-oss-120b` returned 402
  (trial credit), and NVIDIA `poolside/laguna-xs-2.1` returned a 503.
- Passing but disabled by policy: Groq `allam-2-7b` (absent from the free
  plan), Cloudflare `@cf/meta/llama-3.1-70b-instruct` (absent from the current
  listing), and Mistral's retired spellings `open-mistral-nemo`,
  `open-mistral-nemo-2407`, `mistral-tiny-2407`, and `mistral-tiny-latest`.
- Candidates not admitted: nine new Cloudflare listings (400 or 403 for this
  account), Ollama `deepseek-v4.1-flash` (402) and `mistral-large-4` (403),
  NVIDIA `deepseek-ai/deepseek-v4.1-flash` and `z-ai/glm-5.3-flash` (timeouts
  at 60 s). New OpenCode Zen listings were not probed pending its privacy
  review, and new Zhipu, LLM7, and Hugging Face listings were not probed (paid models,
  or no usable credential or credit).

## Metaswarm default panel

Five of the seven default `FREELLMPOOL_STRONG_MODELS` routes were unusable
(`nvidia/moonshotai/kimi-k2.6` 404, `nvidia/mistralai/mistral-large-3-675b-instruct-2512`
410, `mistral/mistral-large-latest` 403, `openrouter/openai/gpt-oss-120b:free`
404, and `nvidia/z-ai/glm-5.1`, which is not in the catalog). The default panel
is now `nvidia/moonshotai/kimi-k3`, `nvidia/nvidia/nemotron-3-ultra-550b-a55b`,
`mistral/mistral-medium-latest`, `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`,
and `openrouter/nvidia/nemotron-3-super-120b-a12b:free`. Every member also exists
in the 0.13.0 catalog, so the adapter works with the current PyPI release;
`z-ai/glm-5.3` is left out until a release ships it. All but the Mistral route
returned non-empty completions in this refresh; `mistral-medium-latest` is an
enabled route whose 429s were rate limiting.
