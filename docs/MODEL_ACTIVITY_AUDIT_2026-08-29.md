# Exhaustive model activity audit — 2026-08-29

This is the route-by-route disposition record for the packaged
[`providers.toml`](../src/freellmpool/providers.toml) snapshot used by the
2026-08-29 maintenance release. It stores no credential values, account
identifiers, request bodies, provider response text, or raw diagnostics.

## Scope, counts, and evidence semantics

The current pass was a catalog/listing reconciliation with bounded targeted
canaries. It was **not** a claim that all 431 chat routes were successfully
completion-tested on 2026-08-29. Public listing presence and request-shaping
compatibility are never treated as successful completion evidence.

| Modality | Provider groups | Cataloged | Enabled | Automatic | Enabled exact-pin-only | Disabled |
|---|---:|---:|---:|---:|---:|---:|
| Chat | 22 | 431 | 177 | 149 | 28 | 254 |
| Embeddings | 5 | 25 | 14 | 14 | 0 | 11 |
| Transcription | 3 | 5 | 5 | 5 | 0 | 0 |
| **All capabilities** | — | **461** | **196** | **168** | **28** | **265** |

Disposition terms have precise runtime meanings:

- **Automatic** means `enabled` is true (or omitted) and `auto` is true (or
  omitted). The route may enter automatic fan-out when its provider is
  configured.
- **Enabled exact-pin-only** means `enabled` is true and `auto = false`. It is
  counted among enabled routes but cannot enter automatic fan-out.
- **Disabled exact-pin-only** means `enabled = false`. It is retained for
  explicit lifecycle/pending-history pins, but is excluded from automatic
  routing and from enabled-route counts.
- **Current completion canary** means the bounded 2026-08-29 pass produced the
  stated repeated non-empty result through the packaged adapter.
- **Listing-only/public evidence** proves only that an identifier or price was
  advertised. It does not prove completion access, a recurring allowance, or
  account eligibility.
- **Historic completion evidence** refers to the committed dated audits. It is
  deliberately not relabeled as a current canary.
- **Uncredentialed gap** means the provider boundary/listing could be checked
  but no usable maintenance credential existed for completion acceptance.

A 401, 402, 429, timeout, provider-wide quota result, or isolated 5xx is not
retirement evidence. Definitive first-party retirement, absent current
inventory, repeat missing/unsupported results, payment-only classification, or
an explicit privacy policy gate can disable a route without a successful
completion call.

## Chat provider evidence and exhaustive disposition inventory

Every chat model in the packaged TOML appears exactly once below. Counts in
each heading are `cataloged / enabled / automatic`. A route's disposition is
configuration state, not a guarantee of future capacity.

### Pollinations (keyless) (`pollinations`): 5 / 3 / 1

**Current public listing; historic success; current failed/inconclusive candidates.** The
authoritative [public model listing](https://text.pollinations.ai/models) still defines
`openai-fast` as the canonical selector and `openai`/`gpt-oss` as aliases. The [2026-08-23
audit](MODEL_ACTIVITY_AUDIT_2026-08-23.md) records three non-empty `gpt-oss` completions;
that is historic evidence, not a new 2026-08-29 success. Bounded 2026-08-29 attempts for the
newly listed `gpt-oss-20b` and `ovh-reasoning` rows produced only sanitized 402, timeout, or
429 classifications across the attempts. Neither candidate is counted as a successful
canary, and both remain disabled.

**Automatic (1)**

- `openai-fast`

**Enabled exact-pin-only (2)**

- `openai`
- `gpt-oss`

**Disabled exact-pin-only (2)**

- `gpt-oss-20b`
- `ovh-reasoning`

### LLM7 (key optional) (`llm7`): 13 / 3 / 3

**Current completion canaries plus historic listing evidence.** The official [model
guide](https://docs.llm7.io/guides/models), [quickstart](https://docs.llm7.io/quickstart),
and [limits](https://docs.llm7.io/limits) describe the key-optional free selectors. The
bounded 2026-08-29 keyless pass returned three non-empty completions for each of `default`,
`fast`, and `codestral-latest`. The exact `gpt-oss:20b` route was unavailable and is
disabled. The remaining disabled names are lifecycle/listing history, not current successful
canaries.

**Automatic (3)**

- `default`
- `fast`
- `codestral-latest`

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (10)**

- `qwen3-235b`
- `kimi-k2.6`
- `minimax-m2.7`
- `mistral-small-3.2`
- `GLM-4.6V-Flash`
- `devstral-small-2:24b`
- `deepseek-v4-flash`
- `deepseek-v3.1:671b-terminus`
- `gemma3:27b`
- `gpt-oss:20b`

### OVHcloud AI Endpoints (keyless) (`ovh`): 13 / 12 / 12

**Historic completion evidence; no fresh broad sweep.** OVHcloud is keyless. The [2026-07-14
audit](MODEL_ACTIVITY_AUDIT_2026-07-14.md) recorded 12 of 13 chat routes answering and the
repeat 404 for `Llama-3.1-8B-Instruct`. The [2026-07-17](MODEL_ACTIVITY_AUDIT_2026-07-17.md)
and [2026-07-29](MODEL_ACTIVITY_AUDIT_2026-07-29.md) sweeps retained the healthy routes. The
2026-08-29 pass did not record a new route-by-route OVH completion sweep, so enabled rows
below are retained on that historic evidence.

**Automatic (12)**

- `Meta-Llama-3_3-70B-Instruct`
- `Qwen3.5-397B-A17B`
- `gpt-oss-120b`
- `Mistral-Small-3.2-24B-Instruct-2506`
- `Mistral-Nemo-Instruct-2407`
- `Qwen3.6-27B`
- `Qwen3-32B`
- `Qwen3.5-9B`
- `Qwen2.5-VL-72B-Instruct`
- `Mistral-7B-Instruct-v0.3`
- `gpt-oss-20b`
- `Qwen3-Coder-30B-A3B-Instruct`

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (1)**

- `Llama-3.1-8B-Instruct`

### Kilo Gateway (keyless) (`kilo`): 21 / 16 / 16

**Current listing and current completion canaries, with historic rows clearly separate.**
The current [Kilo gateway listing
documentation](https://kilo.ai/docs/gateway/models-and-providers) was reconciled first. On
2026-08-29, each of `dots-studio/dots-3-note-preview:free`,
`inclusionai/ling-3.0-flash-fin:free`, `liquid/lfm-2.5-2.6b:free`,
`meituan/longcat-2.0-free`, `minimax/minimax-m2.7:free`,
`nvidia/nemotron-3.5-lightning:free`, `poolside/laguna-s-2.1:free`, and `tencent/hy3:free`
returned three non-empty keyless completions through the packaged adapter. Other enabled
rows rely on the historic July audits. The three Inkling/MiniMax M3 candidates are
listing-only and disabled; Kat Coder and Laguna M.1 remain disabled from historic
failed/unavailable checks.

**Automatic (16)**

- `openrouter/free`
- `kilo-auto/free`
- `poolside/laguna-xs-2.1:free`
- `stepfun/step-3.7-flash:free`
- `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`
- `nvidia/nemotron-3-super-120b-a12b:free`
- `nvidia/nemotron-3-ultra-550b-a55b:free`
- `cohere/north-mini-code:free`
- `dots-studio/dots-3-note-preview:free`
- `inclusionai/ling-3.0-flash-fin:free`
- `liquid/lfm-2.5-2.6b:free`
- `meituan/longcat-2.0-free`
- `minimax/minimax-m2.7:free`
- `nvidia/nemotron-3.5-lightning:free`
- `poolside/laguna-s-2.1:free`
- `tencent/hy3:free`

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (5)**

- `poolside/laguna-m.1:free`
- `minimax/minimax-m3:free`
- `thinkingmachines/inkling-small:free`
- `thinkingmachines/inkling:free`
- `kwaipilot/kat-coder-pro-v2.5:free`

### OpenCode Zen (keyless) (`opencode`): 9 / 0 / 0

**Listing-only and policy-disabled, with limited historic evidence.** The [2026-07-14
audit](MODEL_ACTIVITY_AUDIT_2026-07-14.md) records non-empty completions from five
then-current `-free` routes, but kept them disabled for the privacy/retention opt-in policy.
The current nine-row inventory is still disabled in full. Newly observed rows are
public-listing candidates, not successful 2026-08-29 completion canaries, and historic
success for the older five does not override the policy gate.

**Automatic (0)**

None.

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (9)**

- `deepseek-v4-flash-free`
- `nemotron-3-ultra-free`
- `north-mini-code-free`
- `hy3-free`
- `mimo-v2.5-free`
- `laguna-s-2.1-free`
- `ling-3.0-flash-fin-free`
- `muse-spark-1.2-contributor-free`
- `nemotron-3.5-lightning-free`

### Hugging Face (router) (`huggingface`): 22 / 21 / 21

**Historic completion evidence; current account access not re-proven.** The
[2026-07-14](MODEL_ACTIVITY_AUDIT_2026-07-14.md),
[2026-07-17](MODEL_ACTIVITY_AUDIT_2026-07-17.md), and
[2026-07-29](MODEL_ACTIVITY_AUDIT_2026-07-29.md) audits record successful Hugging Face
routes followed by provider-wide exhaustion of included credits.
`CohereLabs/command-a-vision-07-2025` remains disabled after a historic unsupported
response. The 2026-08-29 refresh did not record fresh credentialed success for every enabled
row; they are retained historic capacity, not a claim of current account entitlement.

**Automatic (21)**

- `Qwen/Qwen3-Coder-30B-A3B-Instruct`
- `Qwen/Qwen3-Coder-Next`
- `Qwen/Qwen3.5-122B-A10B`
- `Qwen/Qwen3.5-35B-A3B`
- `Qwen/Qwen3-4B-Instruct-2507`
- `deepseek-ai/DeepSeek-V4-Flash`
- `deepseek-ai/DeepSeek-V4-Pro`
- `deepseek-ai/DeepSeek-R1`
- `CohereLabs/c4ai-command-a-03-2025`
- `CohereLabs/aya-vision-32b`
- `CohereLabs/aya-expanse-32b`
- `CohereLabs/c4ai-command-r-08-2024`
- `CohereLabs/command-a-reasoning-08-2025`
- `MiniMaxAI/MiniMax-M2.7`
- `MiniMaxAI/MiniMax-M3`
- `Qwen/Qwen3.6-27B`
- `Qwen/Qwen3.6-35B-A3B`
- `XiaomiMiMo/MiMo-V2.5-Pro`
- `google/gemma-4-31B-it`
- `moonshotai/Kimi-K2.7-Code`
- `zai-org/GLM-5.2`

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (1)**

- `CohereLabs/command-a-vision-07-2025`

### Groq (`groq`): 11 / 6 / 5

**Current first-party lifecycle/limit evidence plus bounded credentialed admission.** Groq's
[deprecation history](https://console.groq.com/docs/deprecations) shuts down
`llama-3.3-70b-versatile` and `llama-3.1-8b-instant` for Free and Developer users on
2026-08-16. The [current model list](https://console.groq.com/docs/models) and [free-plan
rate limits](https://console.groq.com/docs/rate-limits) omit `allam-2-7b` and give the
Compound limits used here. Immediately before admission, those first-party pages listed
`qwen/qwen3.8-27b` as a Preview model on the recurring Free plan (1,000 RPD and 2M TPD).
The packaged adapter then produced exactly three sequential, non-empty sanitized chat
canaries and a separate streaming pass. The forced tool canary returned `unsupported`
despite Groq's advertised tool capability. The model is therefore enabled pin-only for
explicit text/stream use, remains excluded from automatic routing, and carries no local
claim of verified tool support. The bounded, secret-free results are retained as
[machine-readable admission evidence](evidence/groq-qwen3.8-27b-admission-2026-08-29.json).
The five automatic routes retain earlier completion
evidence; no fresh success is invented for them here.

**Automatic (5)**

- `openai/gpt-oss-120b`
- `openai/gpt-oss-20b`
- `groq/compound`
- `qwen/qwen3.6-27b`
- `groq/compound-mini`

**Enabled exact-pin-only (1)**

- `qwen/qwen3.8-27b`

**Disabled exact-pin-only (5)**

- `llama-3.3-70b-versatile`
- `llama-3.1-8b-instant`
- `meta-llama/llama-4-scout-17b-16e-instruct`
- `qwen/qwen3-32b`
- `allam-2-7b`

### Aion Labs (`aion`): 5 / 4 / 4

**Public listing only for the refresh; uncredentialed completion gap.** Aion's official
[models](https://www.aionlabs.ai/docs/models/), [pricing](https://www.aionlabs.ai/pricing/),
and [rate-limit documentation](https://www.aionlabs.ai/docs/rate-limits/) support the
provider shape and allowance. The normalized current listing no longer supports
`aion-labs/aion-2.5`, so that row is disabled. No usable maintenance credential was
available for a 2026-08-29 completion sweep. The four enabled rows are
listing-confirmed/retained catalog state, not current credentialed canary successes.

**Automatic (4)**

- `aion-labs/aion-2.0`
- `aion-labs/aion-3.0`
- `aion-labs/aion-3.0-mini`
- `aion-labs/aion-rp-llama-3.1-8b`

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (1)**

- `aion-labs/aion-2.5`

### ModelScope API Inference (`modelscope`): 8 / 6 / 6

**Public listing only for the refresh; uncredentialed completion gap.** The official
[API-Inference documentation](https://modelscope.cn/docs/model-service/API-Inference)
supports the recurring allowance and API shape. The stale unversioned
`deepseek-ai/DeepSeek-V4-Flash` row is disabled, while the exact
`deepseek-ai/DeepSeek-V4-Flash-0731` replacement is cataloged disabled as a listing-only
candidate. No usable maintenance token was available, so the six enabled rows retain
documented/listing state without a new credentialed completion claim.

**Automatic (6)**

- `MiniMax/MiniMax-M3`
- `Qwen/Qwen3.5-27B`
- `Qwen/Qwen3.5-35B-A3B`
- `stepfun-ai/Step-3.7-Flash`
- `Tencent-Hunyuan/Hy3`
- `ZhipuAI/GLM-5.2`

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (2)**

- `deepseek-ai/DeepSeek-V4-Flash`
- `deepseek-ai/DeepSeek-V4-Flash-0731`

### Morph (`morph`): 6 / 0 / 0

**Current first-party pricing; all rows disabled; uncredentialed.** Morph's current
[pricing](https://www.morphllm.com/pricing) and [fast-model
documentation](https://docs.morphllm.com/sdk/components/fast-models) show non-zero pricing
and no longer document the old recurring-free allowance. All six aliases are therefore
disabled exact pins pending an independently verified account entitlement and repeat
completion canaries. No usable maintenance credential was available and no success is
claimed.

**Automatic (0)**

None.

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (6)**

- `morph-glm52-744b`
- `morph-minimax3-428b`
- `morph-glm53-744b`
- `morph-glm53flash`
- `morph-dsv4flash`
- `morph-kimik3`

### Vercel AI Gateway (`vercel`): 13 / 8 / 1

**Current public pricing verification; credentialed acceptance still failed.** The [dated
Vercel acceptance audit](VERCEL_ACCEPTANCE_2026-08-23.md) records a fresh public-only pass
on 2026-08-29: `poolside/laguna-s-2.1-free` had one active Poolside endpoint, 256,000
context, 32,768 maximum output, and numeric zero in every aggregate and endpoint price
field. Three bounded credentialed calls returned sanitized HTTP 403
`customer_verification_required` outcomes—no completion, provenance, or response-cost
acceptance was proven. The one automatic route is a catalog policy disposition, not a
successful current credentialed canary. Priced rows are enabled exact-pin-only; the four new
zero-price candidates and retired suffixed Nemotron row are disabled.

**Automatic (1)**

- `poolside/laguna-s-2.1-free`

**Enabled exact-pin-only (7)**

- `nvidia/nemotron-3.5-lightning`
- `deepseek/deepseek-v4-flash-0731`
- `zai/glm-5.2`
- `minimax/minimax-m3`
- `deepseek/deepseek-v4-pro`
- `moonshotai/kimi-k2.6`
- `xiaomi/mimo-v2.5-pro`

**Disabled exact-pin-only (5)**

- `inclusionai/ling-3.0-flash-fin-free`
- `inclusionai/ling-3.0-flash-fin`
- `minimax/minimax-m2.7-free`
- `minimax/minimax-m3-free`
- `nvidia/nemotron-3.5-lightning-free`

### SiliconFlow (`siliconflow`): 7 / 1 / 1

**First-party free-model documentation; uncredentialed candidates.** The official
[rate-limit
documentation](https://docs.siliconflow.cn/en/userguide/rate-limits/rate-limit-and-upgradation)
and [chat
API](https://docs.siliconflow.cn/en/api-reference/chat-completions/chat-completions) support
the documented `Qwen/Qwen3-8B` free route. A maintenance credential was not available. The
six additional names are disabled listing candidates and have no successful credentialed
canary; the enabled row retains documented historic catalog state.

**Automatic (1)**

- `Qwen/Qwen3-8B`

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (6)**

- `Qwen/Qwen3.5-4B`
- `Qwen/Qwen2.5-7B-Instruct`
- `Qwen/Qwen2.5-Coder-7B-Instruct`
- `THUDM/GLM-4-9B-0414`
- `internlm/internlm2_5-7b-chat`
- `THUDM/glm-4-9b-chat`

### Cerebras (`cerebras`): 5 / 2 / 0

**Current first-party inventory and finite-trial classification; historic completion
evidence only.** The official [model
overview](https://inference-docs.cerebras.ai/models/overview) lists only `gpt-oss-120b` and
`gemma-4-31b`, while [pricing](https://www.cerebras.ai/pricing) describes a finite $5 trial
rather than recurring free capacity. Both current IDs have historic completion evidence in
the [2026-07-14 audit](MODEL_ACTIVITY_AUDIT_2026-07-14.md), but are enabled exact-pin-only
so trial spend never enters automatic fan-out. The other three rows are disabled lifecycle
history.

**Automatic (0)**

None.

**Enabled exact-pin-only (2)**

- `gpt-oss-120b`
- `gemma-4-31b`

**Disabled exact-pin-only (3)**

- `qwen-3-235b-a22b-instruct-2507`
- `llama3.1-8b`
- `zai-glm-4.7`

### NVIDIA NIM (`nvidia`): 101 / 10 / 10

**Current public inventory; candidates listing-only; enabled rows historic.** The live
first-party [`/v1/models` inventory](https://integrate.api.nvidia.com/v1/models) was
reconciled on 2026-08-29. Thirteen formerly enabled chat IDs and five formerly enabled
embedding IDs were absent and were disabled. The newly observed chat candidates
`deepseek-ai/deepseek-v4-flash-0731`, `deepseek-ai/deepseek-v4-pro-0813`,
`meta/muse-glimmer-30b`, `moonshotai/kimi-k3`, and `nvidia/nemotron-3.5-lightning-30b-a3b`
are listing-only and disabled. No fresh credentialed success is claimed for any candidate.
The ten enabled chat rows retain historic completion evidence from the July audits plus
current listing presence.

The 13 **current-list absence disablements** are:

- `deepseek-ai/deepseek-v4-flash`
- `meta/llama-3.1-70b-instruct`
- `meta/llama-3.1-8b-instruct`
- `meta/llama-3.2-3b-instruct`
- `mistralai/mistral-medium-3.5-128b`
- `nvidia/llama-3.1-nemotron-nano-vl-8b-v1`
- `nvidia/llama-3.3-nemotron-super-49b-v1`
- `nvidia/llama-3.3-nemotron-super-49b-v1.5`
- `nvidia/nemotron-nano-12b-v2-vl`
- `nvidia/nvidia-nemotron-nano-9b-v2`
- `stepfun-ai/step-3.7-flash`
- `thinkingmachines/inkling`
- `z-ai/glm-5.2`

The five **current-list, listing-only candidates** are:

- `deepseek-ai/deepseek-v4-flash-0731`
- `deepseek-ai/deepseek-v4-pro-0813`
- `meta/muse-glimmer-30b`
- `moonshotai/kimi-k3`
- `nvidia/nemotron-3.5-lightning-30b-a3b`

All other disabled NVIDIA chat rows below are retained historic lifecycle or
failed-canary records; they are not newly inferred from the current listing.

**Automatic (10)**

- `nvidia/nemotron-3-super-120b-a12b`
- `meta/llama-3.2-11b-vision-instruct`
- `meta/llama-3.2-90b-vision-instruct`
- `minimaxai/minimax-m3`
- `mistralai/mistral-nemotron`
- `nvidia/nemotron-3-nano-30b-a3b`
- `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`
- `openai/gpt-oss-20b`
- `nvidia/nemotron-3-ultra-550b-a55b`
- `poolside/laguna-xs-2.1`

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (91)**

- `meta/llama-3.3-70b-instruct`
- `moonshotai/kimi-k2.6`
- `mistralai/mistral-small-4-119b-2603`
- `deepseek-ai/deepseek-r1`
- `01-ai/yi-large`
- `abacusai/dracarys-llama-3.1-70b-instruct`
- `adept/fuyu-8b`
- `ai21labs/jamba-1.5-large-instruct`
- `aisingapore/sea-lion-7b-instruct`
- `bigcode/starcoder2-15b`
- `bytedance/seed-oss-36b-instruct`
- `databricks/dbrx-instruct`
- `deepseek-ai/deepseek-coder-6.7b-instruct`
- `deepseek-ai/deepseek-v4-flash`
- `deepseek-ai/deepseek-v4-flash-0731`
- `deepseek-ai/deepseek-v4-pro-0813`
- `deepseek-ai/deepseek-v4-pro`
- `google/codegemma-1.1-7b`
- `google/codegemma-7b`
- `google/gemma-2-2b-it`
- `google/gemma-2b`
- `google/gemma-3-12b-it`
- `google/gemma-3-4b-it`
- `google/gemma-3n-e2b-it`
- `google/gemma-3n-e4b-it`
- `google/gemma-4-31b-it`
- `google/recurrentgemma-2b`
- `ibm/granite-3.0-3b-a800m-instruct`
- `ibm/granite-3.0-8b-instruct`
- `ibm/granite-34b-code-instruct`
- `ibm/granite-8b-code-instruct`
- `meta/codellama-70b`
- `meta/llama-3.1-70b-instruct`
- `meta/llama-3.1-8b-instruct`
- `meta/llama-3.2-1b-instruct`
- `meta/llama-3.2-3b-instruct`
- `meta/llama-4-maverick-17b-128e-instruct`
- `meta/llama2-70b`
- `microsoft/phi-3-vision-128k-instruct`
- `microsoft/phi-3.5-moe-instruct`
- `microsoft/phi-4-mini-instruct`
- `microsoft/phi-4-multimodal-instruct`
- `minimaxai/minimax-m2.7`
- `mistralai/codestral-22b-instruct-v0.1`
- `mistralai/ministral-14b-instruct-2512`
- `mistralai/mistral-7b-instruct-v0.3`
- `mistralai/mistral-large`
- `mistralai/mistral-large-2-instruct`
- `mistralai/mistral-large-3-675b-instruct-2512`
- `mistralai/mistral-medium-3.5-128b`
- `mistralai/mixtral-8x22b-v0.1`
- `mistralai/mixtral-8x7b-instruct-v0.1`
- `nv-mistralai/mistral-nemo-12b-instruct`
- `nvidia/cosmos-reason2-8b`
- `nvidia/llama-3.1-nemotron-51b-instruct`
- `nvidia/llama-3.1-nemotron-70b-instruct`
- `nvidia/llama-3.1-nemotron-nano-8b-v1`
- `nvidia/llama-3.1-nemotron-nano-vl-8b-v1`
- `nvidia/llama-3.1-nemotron-ultra-253b-v1`
- `nvidia/llama-3.3-nemotron-super-49b-v1`
- `nvidia/llama-3.3-nemotron-super-49b-v1.5`
- `nvidia/llama3-chatqa-1.5-70b`
- `nvidia/mistral-nemo-minitron-8b-8k-instruct`
- `nvidia/nemotron-4-340b-instruct`
- `nvidia/nemotron-mini-4b-instruct`
- `nvidia/nemotron-nano-12b-v2-vl`
- `nvidia/nemotron-nano-3-30b-a3b`
- `nvidia/neva-22b`
- `nvidia/nvclip`
- `nvidia/nvidia-nemotron-nano-9b-v2`
- `nvidia/vila`
- `openai/gpt-oss-120b`
- `qwen/qwen3-coder-480b-a35b-instruct`
- `qwen/qwen3-next-80b-a3b-instruct`
- `qwen/qwen3.5-122b-a10b`
- `qwen/qwen3.5-397b-a17b`
- `sarvamai/sarvam-m`
- `stepfun-ai/step-3.5-flash`
- `stepfun-ai/step-3.7-flash`
- `stockmark/stockmark-2-100b-instruct`
- `upstage/solar-10.7b-instruct`
- `writer/palmyra-creative-122b`
- `writer/palmyra-fin-70b-32k`
- `writer/palmyra-med-70b`
- `writer/palmyra-med-70b-32k`
- `z-ai/glm-5.2`
- `zyphra/zamba2-7b-instruct`
- `thinkingmachines/inkling`
- `meta/muse-glimmer-30b`
- `moonshotai/kimi-k3`
- `nvidia/nemotron-3.5-lightning-30b-a3b`

### OpenRouter (free models) (`openrouter`): 40 / 8 / 8

**Current free listing reconciliation; new candidates listing-only; enabled rows historic.**
The current first-party [model inventory](https://openrouter.ai/models) no longer supports
four formerly enabled free IDs, so they were disabled. Ten newly observed `:free` candidates
are cataloged disabled pending repeat credentialed completions. The eight enabled rows
retain historic July evidence; the 2026-08-29 pass does not claim a new successful
completion sweep, and public listing presence alone did not enable any candidate.

The four **no-longer-free listing disablements** are:

- `nvidia/nemotron-3-nano-30b-a3b:free`
- `nvidia/nemotron-nano-12b-v2-vl:free`
- `nvidia/nemotron-nano-9b-v2:free`
- `openai/gpt-oss-20b:free`

The ten **current-list, listing-only candidates** are:

- `dots-studio/dots-3-note-preview:free`
- `inclusionai/ling-3.0-flash-fin:free`
- `liquid/lfm-2.5-2.6b:free`
- `minimax/minimax-m2.7:free`
- `minimax/minimax-m3:free`
- `nvidia/nemotron-3.5-lightning:free`
- `poolside/laguna-s-2.1:free`
- `thinkingmachines/inkling-small:free`
- `thinkingmachines/inkling:free`
- `z-ai/glm-5.2:free`

All other disabled OpenRouter rows below are retained historic lifecycle or
failed-canary records.

**Automatic (8)**

- `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`
- `poolside/laguna-xs-2.1:free`
- `google/gemma-4-26b-a4b-it:free`
- `google/gemma-4-31b-it:free`
- `nvidia/nemotron-3-super-120b-a12b:free`
- `openrouter/free`
- `nvidia/nemotron-3-ultra-550b-a55b:free`
- `cohere/north-mini-code:free`

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (32)**

- `meta-llama/llama-3.3-70b-instruct:free`
- `qwen/qwen3-next-80b-a3b-instruct:free`
- `qwen/qwen3-coder:free`
- `deepseek/deepseek-chat-v3.1:free`
- `z-ai/glm-4.5-air:free`
- `nousresearch/hermes-3-llama-3.1-405b:free`
- `openrouter/owl-alpha`
- `poolside/laguna-m.1:free`
- `moonshotai/kimi-k2.6:free`
- `nex-agi/nex-n2-pro:free`
- `google/lyria-3-pro-preview`
- `google/lyria-3-clip-preview`
- `liquid/lfm-2.5-1.2b-thinking:free`
- `liquid/lfm-2.5-1.2b-instruct:free`
- `nvidia/nemotron-3-nano-30b-a3b:free`
- `nvidia/nemotron-nano-12b-v2-vl:free`
- `nvidia/nemotron-nano-9b-v2:free`
- `openai/gpt-oss-120b:free`
- `openai/gpt-oss-20b:free`
- `cognitivecomputations/dolphin-mistral-24b-venice-edition:free`
- `meta-llama/llama-3.2-3b-instruct:free`
- `tencent/hy3:free`
- `dots-studio/dots-3-note-preview:free`
- `inclusionai/ling-3.0-flash-fin:free`
- `liquid/lfm-2.5-2.6b:free`
- `minimax/minimax-m2.7:free`
- `minimax/minimax-m3:free`
- `nvidia/nemotron-3.5-lightning:free`
- `poolside/laguna-s-2.1:free`
- `thinkingmachines/inkling-small:free`
- `thinkingmachines/inkling:free`
- `z-ai/glm-5.2:free`

### Google Gemini (`gemini`): 8 / 1 / 1

**Current first-party pricing/listing; uncredentialed completion gap.** Google's
[pricing](https://ai.google.dev/gemini-api/docs/pricing), [model
list](https://ai.google.dev/gemini-api/docs/models), and [deprecation
table](https://ai.google.dev/gemini-api/docs/deprecations) identify the current Standard
free-tier Flash IDs and the retired 2.0 routes. `gemini-2.5-flash` retains its existing
automatic disposition. The 3.1/3.5/3.6/3.7 candidates are listing-only, disabled, and
excluded from automatic routing pending a real free-project canary. No usable key was
available. Adapter request shaping and the 4,096-token thinking floor are compatibility
controls, not acceptance evidence.

**Automatic (1)**

- `gemini-2.5-flash`

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (7)**

- `gemini-2.0-flash`
- `gemini-2.0-flash-lite`
- `gemini-3.1-flash-lite`
- `gemini-3.5-flash`
- `gemini-3.5-flash-lite`
- `gemini-3.6-flash`
- `gemini-3.7-flash`

### Cloudflare Workers AI (`cloudflare`): 27 / 21 / 21

**Current first-party billing/lifecycle evidence and one current completion canary.**
Cloudflare's official [model
documentation](https://developers.cloudflare.com/workers-ai/models/) and
[pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/) place Kimi 2.6/2.7
behind Workers Paid and mark the Llama 3.1 70B route deprecated; those rows are disabled.
`@cf/qwen/qwen3.8-27b` returned three non-empty completions through the packaged adapter on
2026-08-29 and is enabled. All other enabled rows rely on historic catalog/canary evidence
rather than a fresh full sweep.

**Automatic (21)**

- `@cf/meta/llama-3.3-70b-instruct-fp8-fast`
- `@cf/mistralai/mistral-small-3.1-24b-instruct`
- `@cf/openai/gpt-oss-120b`
- `@cf/google/gemma-2b-it-lora`
- `@cf/meta/llama-3.2-3b-instruct`
- `@cf/mistral/mistral-7b-instruct-v0.2-lora`
- `@cf/deepseek-ai/deepseek-r1-distill-qwen-32b`
- `@cf/meta/llama-3.1-8b-instruct-fp8`
- `@cf/meta/llama-3.2-1b-instruct`
- `@cf/zai-org/glm-4.7-flash`
- `@cf/ibm-granite/granite-4.0-h-micro`
- `@cf/qwen/qwen2.5-coder-32b-instruct`
- `@cf/nvidia/nemotron-3-120b-a12b`
- `@cf/aisingapore/gemma-sea-lion-v4-27b-it`
- `@cf/qwen/qwen3-30b-a3b-fp8`
- `@cf/qwen/qwen3.8-27b`
- `@cf/google/gemma-7b-it-lora`
- `@cf/google/gemma-4-26b-a4b-it`
- `@cf/openai/gpt-oss-20b`
- `@cf/meta/llama-4-scout-17b-16e-instruct`
- `@cf/qwen/qwq-32b`

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (6)**

- `@cf/google/gemma-3-12b-it`
- `@cf/meta/llama-3.1-70b-instruct`
- `@cf/moonshotai/kimi-k2.6`
- `@cf/moonshotai/kimi-k2.7-code`
- `@cf/meta-llama/llama-2-7b-chat-hf-lora`
- `@cf/meta/llama-3.2-11b-vision-instruct`

### Mistral (`mistral`): 42 / 31 / 15

**Current first-party lifecycle reconciliation; pin-only aliases; no fresh full canary.**
The official [model catalog](https://docs.mistral.ai/models) and [lifecycle
policy](https://docs.mistral.ai/inference/model-lifecycle) distinguish current, Labs, alias,
and retired IDs. Invalid/retired spellings are disabled. Code, Vibe, Devstral, and Magistral
variants whose workload/access posture is not appropriate for generic fan-out remain enabled
exact-pin-only. `zai-glm-5-2` and `labs-leanstral-1-5` are disabled listing candidates
pending credentialed canaries. Historic July sweeps support retained routes; no 2026-08-29
route-by-route completion success is claimed.

**Automatic (15)**

- `mistral-small-latest`
- `codestral-latest`
- `mistral-medium-latest`
- `codestral-2508`
- `mistral-small-2603`
- `mistral-large-2512`
- `mistral-large-latest`
- `ministral-3b-2512`
- `ministral-3b-latest`
- `ministral-8b-2512`
- `ministral-8b-latest`
- `ministral-14b-2512`
- `ministral-14b-latest`
- `mistral-medium-3-5`
- `mistral-medium-3`

**Enabled exact-pin-only (16)**

- `mistral-medium-2505`
- `mistral-medium-2508`
- `mistral-vibe-cli-with-tools`
- `mistral-code-latest`
- `mistral-code-fim-latest`
- `devstral-2512`
- `devstral-medium-latest`
- `devstral-latest`
- `mistral-code-agent-latest`
- `mistral-vibe-cli-fast`
- `magistral-small-latest`
- `magistral-medium-2509`
- `magistral-medium-latest`
- `mistral-vibe-cli-latest`
- `magistral-small-2509`
- `mistral-small-2506`

**Disabled exact-pin-only (11)**

- `open-mistral-nemo`
- `mistral-medium`
- `open-mistral-nemo-2407`
- `mistral-tiny-2407`
- `mistral-tiny-latest`
- `labs-leanstral-2603`
- `mistral-medium-3.5`
- `mistral-medium-2604`
- `mistral-medium-c21211-r0-75`
- `zai-glm-5-2`
- `labs-leanstral-1-5`

### Cohere (`cohere`): 16 / 16 / 16

**Historic completion evidence; no fresh full canary.** The [2026-07-14
audit](MODEL_ACTIVITY_AUDIT_2026-07-14.md) records all 15 then-current routes answering and
`command-a-translate-08-2025` passing before addition; the [2026-07-17
audit](MODEL_ACTIVITY_AUDIT_2026-07-17.md) retained the full group. The current 16 automatic
rows are therefore historic catalog evidence, not a claim that all were re-run on
2026-08-29.

**Automatic (16)**

- `command-r-plus-08-2024`
- `command-r-08-2024`
- `c4ai-aya-expanse-32b`
- `c4ai-aya-vision-32b`
- `command-a-03-2025`
- `command-a-plus-05-2026`
- `command-a-reasoning-08-2025`
- `command-a-vision-07-2025`
- `north-mini-code-1-0`
- `command-r7b-12-2024`
- `command-r7b-arabic-02-2025`
- `tiny-aya-earth`
- `tiny-aya-fire`
- `tiny-aya-global`
- `tiny-aya-water`
- `command-a-translate-08-2025`

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (0)**

None.

### SambaNova (`sambanova`): 9 / 0 / 0

**Current listing/history only; payment gate; no successful current canary.** Historic
auditing found an isolated Gemma response followed by repeat payment requirements, which was
not enough to enable it. Every SambaNova row remains disabled. `MiniMax-M3` is retained as a
current listing-only candidate; it was not credentialed and is not counted as a successful
canary.

**Automatic (0)**

None.

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (9)**

- `Meta-Llama-3.3-70B-Instruct`
- `DeepSeek-V3.1`
- `DeepSeek-V3.2`
- `Llama-4-Maverick-17B-128E-Instruct`
- `MiniMax-M2.7`
- `gemma-3-12b-it`
- `gemma-4-31B-it`
- `gpt-oss-120b`
- `MiniMax-M3`

### Z.ai / Zhipu GLM (`zhipu`): 2 / 2 / 1

**Current first-party free-Flash lifecycle evidence; historic completion evidence.** Both
Flash IDs answered in the [2026-07-14 audit](MODEL_ACTIVITY_AUDIT_2026-07-14.md). Current
first-party model information retains 4.7 Flash while 4.5 Flash approaches shutdown; 4.5 is
therefore enabled exact-pin-only and 4.7 remains automatic. Neither row is presented as a
fresh 2026-08-29 completion.

**Automatic (1)**

- `glm-4.7-flash`

**Enabled exact-pin-only (1)**

- `glm-4.5-flash`

**Disabled exact-pin-only (0)**

None.

### Ollama Cloud (`ollama`): 48 / 7 / 7

**Current listing/subscription checks plus historic enabled-route evidence.** Historic July
sweeps established the surviving free routes and disabled explicit retirements. Current
discovery exposed newer frontier names, but subscription requirements are not free
completion evidence. `minimax-m2.5` is now disabled, and `deepseek-v4-flash:0731`,
`deepseek-v4-pro:0813`, `glm-5.2`, `glm-5.3`, `glm-5.3-flash`, `kimi-k2.7-code`, and
`kimi-k3` are disabled listing-only or subscription-gated candidates. The seven enabled rows
retain historic evidence; no fresh success is invented.

**Automatic (7)**

- `gpt-oss:120b`
- `gemma4:31b`
- `nemotron-3-super`
- `nemotron-3-ultra`
- `nemotron-3-nano:30b`
- `gpt-oss:20b`
- `minimax-m3`

**Enabled exact-pin-only (0)**

None.

**Disabled exact-pin-only (41)**

- `qwen3-coder:480b`
- `kimi-k2.6`
- `minimax-m2.7`
- `ministral-3:3b`
- `gemini-3-flash-preview`
- `gemma3:4b`
- `rnj-1:8b`
- `qwen3-vl:235b-instruct`
- `qwen3-vl:235b`
- `deepseek-v3.2`
- `gemma3:27b`
- `kimi-k2:1t`
- `qwen3-coder-next`
- `deepseek-v4-flash`
- `minimax-m2.1`
- `devstral-2:123b`
- `glm-5`
- `minimax-m2`
- `mistral-large-3:675b`
- `devstral-small-2:24b`
- `gemma3:12b`
- `cogito-2.1:671b`
- `glm-4.6`
- `glm-4.7`
- `kimi-k2-thinking`
- `deepseek-v4-pro`
- `deepseek-v3.1:671b`
- `qwen3.5:397b`
- `glm-5.1`
- `kimi-k2.5`
- `qwen3-next:80b`
- `ministral-3:8b`
- `ministral-3:14b`
- `minimax-m2.5`
- `deepseek-v4-flash:0731`
- `deepseek-v4-pro:0813`
- `glm-5.2`
- `glm-5.3`
- `glm-5.3-flash`
- `kimi-k2.7-code`
- `kimi-k3`

## Removed provider groups

Removed groups are not part of the 22-provider or 431-chat-route totals:

- **GitHub Models:** GitHub's
  [retirement notice](https://github.blog/changelog/2026-07-30-github-models-is-now-retired/)
  retires the catalog, inference API, and BYOK access. The official models,
  chat, and embeddings endpoint classes returned HTTP 410 with the sanitized
  `github_models_retirement_brownout` classification. The chat provider and
  embedder were removed rather than presented as current disabled capacity.
- **LongCat direct:** first-party model documentation lists LongCat 2.0 and
  first-party pricing is pay-as-you-go. With no recurring free direct route,
  the direct provider group was removed. Kilo's separately hosted
  `meituan/longcat-2.0-free` route is independent and passed the Kilo canary
  stated above.

## Embedding evidence and exhaustive disposition inventory

The packaged snapshot has **5 embedding groups, 25
cataloged embedding routes, and 14 enabled/automatic routes**.
The removed GitHub embedder is covered in the preceding section.

Evidence age by group:

- **Cohere:** all enabled capability routes passed the historic 2026-07-17
  sweep; no fresh 2026-08-29 route-by-route success is claimed.
- **Cloudflare:** this route passed in the historic 2026-07-17 sweep and saw a
  transient 429 in the 2026-07-29 sweep; the latter was not retirement
  evidence. No fresh current success is claimed.
- **Mistral:** enabled embedding routes passed the historic capability sweeps;
  no fresh 2026-08-29 route-by-route success is claimed.
- **NVIDIA:** the live 2026-08-29 model inventory was authoritative for
  listing state. Five formerly enabled IDs—`baai/bge-m3`,
  `nvidia/llama-nemotron-embed-1b-v2`, `nvidia/nv-embed-v1`,
  `nvidia/nv-embedcode-7b-v1`, and `nvidia/nv-embedqa-e5-v5`—were absent and
  disabled. The one enabled ID is retained from historic capability evidence
  plus current listing presence; it was not relabeled as a fresh successful
  canary. Other disabled rows are historic lifecycle records or listing-only
  candidates.
- **OVHcloud:** `bge-multilingual-gemma2` and `bge-m3` were live-validated on
  2026-06-09; `Qwen3-Embedding-8B` returned HTTP 200 through the embeddings
  endpoint on 2026-07-16. These are historic successes, not fresh canaries.

### Cohere (`cohere`): 5 cataloged / 5 enabled

- `embed-v4.0` — **automatic**
- `embed-english-light-v3.0` — **automatic**
- `embed-english-v3.0` — **automatic**
- `embed-multilingual-light-v3.0` — **automatic**
- `embed-multilingual-v3.0` — **automatic**

### Cloudflare Workers AI (`cloudflare`): 1 cataloged / 1 enabled

- `@cf/baai/bge-base-en-v1.5` — **automatic**

### Mistral (`mistral`): 4 cataloged / 4 enabled

- `mistral-embed` — **automatic**
- `mistral-embed-2312` — **automatic**
- `codestral-embed` — **automatic**
- `codestral-embed-2505` — **automatic**

### NVIDIA NIM (`nvidia`): 12 cataloged / 1 enabled

- `baai/bge-m3` — **disabled exact-pin-only**
- `nvidia/embed-qa-4` — **disabled exact-pin-only**
- `nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1` — **disabled exact-pin-only**
- `nvidia/llama-3.2-nv-embedqa-1b-v1` — **disabled exact-pin-only**
- `nvidia/llama-nemotron-embed-1b-v2` — **disabled exact-pin-only**
- `nvidia/llama-nemotron-embed-vl-1b-v2` — **automatic**
- `nvidia/nv-embed-v1` — **disabled exact-pin-only**
- `nvidia/nv-embedcode-7b-v1` — **disabled exact-pin-only**
- `nvidia/nv-embedqa-e5-v5` — **disabled exact-pin-only**
- `nvidia/nemotron-3-embed-1b` — **disabled exact-pin-only**
- `nvidia/nv-embedqa-mistral-7b-v2` — **disabled exact-pin-only**
- `snowflake/arctic-embed-l` — **disabled exact-pin-only**

### OVHcloud (keyless) (`ovh`): 3 cataloged / 3 enabled

- `bge-multilingual-gemma2` — **automatic**
- `bge-m3` — **automatic**
- `Qwen3-Embedding-8B` — **automatic**

## Transcription evidence and exhaustive disposition inventory

The packaged snapshot has **3 transcription groups and
5 cataloged routes; all 5 are enabled and
automatic**. All five passed the historic 2026-07-17 and 2026-07-29
capability sweeps. The 2026-06-08 known-speech WER smoke established the
quality-first ordering recorded in the catalog. No route is represented as a
fresh 2026-08-29 transcription canary.

### Mistral (Voxtral) (`mistral`): 1 cataloged / 1 enabled

- `voxtral-mini-latest` — **automatic**

### Groq (Whisper) (`groq`): 2 cataloged / 2 enabled

- `whisper-large-v3` — **automatic**
- `whisper-large-v3-turbo` — **automatic**

### OVHcloud (Whisper, keyless) (`ovh`): 2 cataloged / 2 enabled

- `whisper-large-v3` — **automatic**
- `whisper-large-v3-turbo` — **automatic**

## Admission and re-enable policy

New or recovered routes remain disabled until bounded calls through the
packaged adapter produce repeat non-empty completions. Billing-sensitive
gateways must also return recognized serving-provider provenance and the
expected numeric cost. Listing presence, a compatible request shape, one
isolated response, or ambiguous/transient status is insufficient.

The exhaustive inventory can be reproduced from the packaged TOML by treating
omitted `enabled` and `auto` values as true, then partitioning each model into
the three mutually exclusive states defined above. The assertions used to
generate this record fail unless the frozen totals are exactly
**22 / 431 / 178 / 149** for chat, **25 / 14** for embeddings, and
**5 / 5** for transcription.
