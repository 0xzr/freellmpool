# FAQ

## Where do my prompts go?

freellmpool is local software. It does not send prompts through a freellmpool
hosted relay. For each request, the router chooses one configured target from
the provider catalog and sends your prompt directly to that provider's
`base_url`. If that attempt fails, it tries the next eligible provider.

The built-in chat provider list below comes from
[`src/freellmpool/providers.toml`](src/freellmpool/providers.toml). Jurisdiction
and privacy notes are intentionally conservative: if freellmpool's catalog does
not encode data residency, this table says so instead of guessing.

| Provider id | Destination in the catalog | Access | Jurisdiction or region, where stated | Practical privacy posture |
|---|---|---:|---|---|
| `pollinations` | `https://text.pollinations.ai/openai` | Keyless | Not stated in the catalog; Pollinations publishes privacy and terms pages. | Anonymous keyless endpoint; do not send secrets unless Pollinations' current policy fits your use case. |
| `llm7` | `https://api.llm7.io/v1` | Key optional | LLM7's privacy policy says it operates from the United Kingdom. | Optional-token gateway; prompts go to LLM7 and whatever upstream serving path it uses. |
| `ovh` | `https://oai.endpoints.kepler.ai.cloud.ovh.net/v1` | Keyless | OVHcloud describes AI Endpoints as a privacy-focused OVHcloud service; the catalog does not pin a region. | Strongest keyless privacy posture in this catalog, but still a third-party hosted API. |
| `kilo` | `https://api.kilo.ai/api/gateway` | Keyless | Kilo Code Inc.; endpoint region is not stated in the catalog. | Gateway docs warn not to submit personal or confidential data; treat free routes as logged. |
| `opencode` | `https://opencode.ai/zen/v1` | Keyless, disabled by default | OpenCode Zen endpoint region is not stated in the catalog. | Anonymous gateway; cataloged for explicit opt-in only until its privacy/retention posture is reviewed. |
| `huggingface` | `https://router.huggingface.co/v1` | `HF_TOKEN` | Hugging Face says the company and servers are in the United States and data may be processed elsewhere. | Router endpoint; your key and prompt go to Hugging Face's routed inference service. |
| `groq` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` | Groq, Inc.; endpoint region is not stated in the catalog. | Direct provider API using your Groq key; check Groq's current service/privacy terms for retention. |
| `cerebras` | `https://api.cerebras.ai/v1` | `CEREBRAS_API_KEY` (finite $5 trial) | Cerebras Systems Inc.; endpoint region is not stated in the catalog. | Direct provider API using your Cerebras key; the documented models are explicit pins, not recurring automatic capacity. |
| `nvidia` | `https://integrate.api.nvidia.com/v1` | `NVIDIA_API_KEY` | NVIDIA describes worldwide services; endpoint region is not stated in the catalog. | Direct hosted NIM/API endpoint, not local NIM; prompts leave your machine. |
| `openrouter` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | OpenRouter is a routing layer; downstream provider jurisdiction depends on the selected model route. | Extra aggregator hop; OpenRouter has privacy controls, but upstream provider logging can still matter. |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta` | `GEMINI_API_KEY` | Google API/project terms apply; catalog does not pin a region. | Direct Google Gemini API using your key; use Google Cloud controls/settings if you need them. |
| `cloudflare` | `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1` | `CLOUDFLARE_API_TOKEN` plus account id | Cloudflare's Workers AI docs say customer data is processed to provide the service; catalog does not pin a region. | Direct Cloudflare Workers AI endpoint scoped to your account. |
| `mistral` | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` | Mistral AI publishes EU/GDPR-oriented privacy docs; catalog does not pin a region. | Direct Mistral API using your key; check Mistral's current API data controls. |
| `cohere` | `https://api.cohere.ai/compatibility/v1` | `COHERE_API_KEY` | Cohere policy/enterprise commitments apply; catalog does not pin a region. | Direct Cohere API using your trial/API key; training controls vary by account type and settings. |
| `sambanova` | `https://api.sambanova.ai/v1` | `SAMBANOVA_API_KEY` | SambaNova's policy references processors in the US, UK, EEA, Middle East, and Asia Pacific. | Direct SambaNova API using your key; note that current catalog chat models are disabled for auto-routing. |
| `zhipu` | `https://api.z.ai/api/paas/v4` | `ZHIPU_API_KEY` | Z.ai's policy applies; catalog does not pin a region. | Direct Z.ai API; their policy says user content is processed to provide the service. |
| `ollama` | `https://ollama.com/v1` | `OLLAMA_API_KEY` | Ollama Cloud policy/blog statements apply; catalog does not pin a region. | This is Ollama Cloud, not local Ollama. Prompts leave your machine when this provider is used. |
| `aion` | `https://api.aionlabs.ai/v1` | `AION_API_KEY` | Aion Labs is operated by Deep Forge sp. z o.o. in Poland and routes to upstream model infrastructure. | Direct Aion API using your key; its terms permit forwarding prompts to upstream providers to deliver the service. |
| `modelscope` | `https://api-inference.modelscope.cn/v1` | `MODELSCOPE_API_KEY` | ModelScope is an Alibaba open-model platform; the catalog does not pin an inference region. | Hosted API Inference using your token; do not assume local execution or a specific data-residency region. |
| `morph` | `https://api.morphllm.com/v1` | `MORPH_API_KEY` | Morph's privacy policy applies; the catalog does not pin a serving region. | Hosted OpenAI-compatible inference using your key; prompts leave your machine. |
| `vercel` | `https://ai-gateway.vercel.sh/v1` | `AI_GATEWAY_API_KEY` | Vercel AI Gateway routes requests to upstream model providers, whose jurisdictions may vary. | Extra gateway hop; Vercel and the selected upstream provider process each request. |
| `siliconflow` | `https://api.siliconflow.cn/v1` | `SILICONFLOW_API_KEY` | SiliconFlow is operated by Beijing SiliconFlow Technology Co., Ltd.; identity verification is required for free models. | Direct SiliconFlow API; its English privacy policy says it does not store interaction data with open-source models, but verify the current policy for your account and region. |

## Why aren't GitHub Models and LongCat included?

GitHub [retired GitHub Models on July 30, 2026](https://github.blog/changelog/2026-07-30-github-models-is-now-retired/),
including its model catalog, inference API, and BYOK access. On 2026-08-29,
the official `/models`, `/chat/completions`, and `/embeddings` endpoints each
returned HTTP `410` with code `github_models_retirement_brownout`. freellmpool
therefore removed both the GitHub chat provider and GitHub embedder instead of
treating the lifecycle event as a transient outage.

LongCat's current [model documentation](https://longcat.chat/platform/docs/api/models)
lists only LongCat 2.0, and its [pricing page](https://longcat.chat/platform/docs/Pricing/LongCat-2.0.html)
describes pay-as-you-go usage rather than a recurring free route. The retired
LongCat provider is therefore no longer part of the catalog.

Two local storage details matter:

- Quota and lifetime stats are local counters. They record provider/model usage,
  token counts when returned, cache hits, and savings estimates.
- Response caching is off unless you enable it with `FREELLMPOOL_CACHE_TTL` or
  `[settings] cache_ttl`; when enabled, identical responses are stored in a local
  SQLite cache under your user config directory or configured cache path.

## Does freellmpool share my API keys?

No. Keys are read from your environment or local config file and sent only as the
`Authorization: Bearer ...` header, or the provider-specific equivalent, for the
provider being called. Keyless providers send no auth header.

The code does not upload your keys to a freellmpool service, and there is no key
pool shared between users.

## What is the ToS posture?

freellmpool is a router over providers you are allowed to use:

- Your own provider keys stay your own provider keys.
- Keyless providers are used as keyless public endpoints.
- Per-day request hints in the catalog are used to spread load, and real `429`
  responses are honored at request time.
- freellmpool does not create accounts, rotate identities, solve captchas,
  share keys, bypass provider controls, or evade provider limits.

You are still responsible for each provider's terms. In particular, `tokenmax`
intentionally fans out to many models and can burn free-tier quota quickly.

## What happens when a provider rate-limits, fails, or bans me?

For normal non-streaming calls, freellmpool tries candidate providers in order
until one returns a usable, non-empty answer. A provider `429` marks that provider
on cooldown for the current process, skips that provider's other models for the
same request, and continues to the next provider. `408`, `429`, and `5xx`
responses are retryable transport/provider failures; other `4xx` responses are
remembered as client/provider errors, but the router still tries other providers.

If every candidate fails, the CLI reports `AllProvidersExhausted` with the
attempt reasons. For streaming, failover can happen before the first token; once
tokens are flowing, freellmpool cannot switch providers mid-stream without
corrupting the response.

If a provider bans or disables your account/key/IP, freellmpool can route around
it only when another configured provider is available. It cannot unban you or
make a banned key legitimate.

## How reliable is failover?

It is useful but not magic. Failover helps with transient outages, rate limits,
empty completions, malformed responses, context-window mismatches, and transport
errors. It does not guarantee that all providers are equivalent: models differ in
quality, context length, tool support, moderation, and latency.

The most reliable setup is to add several legitimate free keys, then use
`freellmpool capacity status`, `freellmpool providers health`, and
`freellmpool benchmark` to see what is currently usable.

## How is this different from OpenRouter, LiteLLM, and FreeLLMAPI?

- OpenRouter is a hosted routing service with paid usage and some free models.
  freellmpool is local OSS that can start keyless and can also use OpenRouter as
  one provider in the pool.
- LiteLLM is a mature multi-provider SDK/proxy for bring-your-own-key routing.
  freellmpool is narrower: it focuses on pooling legitimate free tiers, keyless
  first-run UX, a CLI, local proxy, MCP server, transcription, and quota-aware
  failover.
- FreeLLMAPI predates this project; the ideas converged independently. The
  [README comparison table](README.md#how-it-compares) keeps the comparison
  gracious and feature-specific.

## Can I get banned for using freellmpool?

Yes, if your usage violates a provider's rules or trips abuse/rate-limit systems.
freellmpool does not give special permission to exceed free-tier limits. The safe
posture is to use your own keys, avoid confidential data unless the selected
provider's policy allows it, keep request volume reasonable, and remove any
provider that tells you to stop.

## Sources

Code behavior:

- Provider list and endpoints:
  [`src/freellmpool/providers.toml`](src/freellmpool/providers.toml)
- Provider configuration/keyless detection:
  [`src/freellmpool/models.py`](src/freellmpool/models.py) and
  [`src/freellmpool/config.py`](src/freellmpool/config.py)
- Routing, cooldown, quota, and failover:
  [`src/freellmpool/router.py`](src/freellmpool/router.py)
- HTTP request shaping and auth headers:
  [`src/freellmpool/client.py`](src/freellmpool/client.py)
- Key setup notes:
  [`docs/ACCOUNTS.md`](docs/ACCOUNTS.md)

Provider policy links checked for this FAQ:

- Pollinations: <https://pollinations.ai/privacy>, <https://pollinations.ai/terms>
- LLM7: <https://github.com/chigwell/llm7.io/blob/main/PRIVACY.md>
- OVHcloud AI Endpoints: <https://www.ovhcloud.com/en/public-cloud/ai-endpoints/>
- Kilo Gateway: <https://kilo.ai/docs/gateway/models-and-providers>
- Hugging Face: <https://huggingface.co/privacy>
- Groq: <https://groq.com/privacy-policy>
- Cerebras: <https://www.cerebras.ai/privacy-policy>,
  <https://www.cerebras.ai/pricing>
- NVIDIA: <https://www.nvidia.com/en-us/about-nvidia/privacy-policy/>
- OpenRouter: <https://openrouter.ai/docs/guides/privacy/provider-logging>,
  <https://openrouter.ai/docs/guides/features/zdr>
- Google Gemini API: <https://ai.google.dev/gemini-api/terms>
- Cloudflare Workers AI: <https://developers.cloudflare.com/workers-ai/platform/data-usage/>
- Mistral AI: <https://docs.mistral.ai/admin/security-access/privacy>
- Cohere: <https://cohere.com/privacy>,
  <https://cohere.com/enterprise-data-commitments>
- SambaNova: <https://sambanova.ai/privacy-policy>
- Z.ai: <https://docs.z.ai/legal-agreement/privacy-policy>
- Ollama Cloud: <https://ollama.com/blog/cloud-models>
- Aion Labs: <https://www.aionlabs.ai/terms/>,
  <https://www.aionlabs.ai/docs/rate-limits/>
- ModelScope API Inference: <https://modelscope.cn/docs/model-service/API-Inference>
- Morph pricing and fast models: <https://www.morphllm.com/pricing>, <https://docs.morphllm.com/sdk/components/fast-models>
- Vercel AI Gateway pricing: <https://vercel.com/docs/ai-gateway/pricing>
- SiliconFlow: <https://docs.siliconflow.cn/en/legals/privacy-policy>,
  <https://docs.siliconflow.cn/en/userguide/rate-limits/rate-limit-and-upgradation>
