# Provider and model refresh — 2026-07-16

This maintenance pass checked the packaged catalog against provider-owned model
lists and documentation. Anonymous routes received a harmless `Reply with the
single word: pong` request where practical. Newly added keyed providers were
documentation-verified only because no credentials were available; they should
not be described as completion-tested.

## Catalog changes

| Provider | Evidence and action |
|---|---|
| LLM7 | Its public model list now includes `gpt-oss:20b`, which returned a keyless completion and was enabled. `gemma3:27b` repeatedly returned an unavailable-model response and was disabled. |
| OVHcloud AI Endpoints | The public list added `Qwen3-Embedding-8B`; its embeddings endpoint returned HTTP 200, so it was added to the embedding catalog. |
| Aion Labs | Added five current chat models from Aion's official model list. Aion documents an OpenAI-compatible API and a no-card free tier of 15 requests/minute and 20,000 tokens/day. |
| ModelScope | Added a curated set of seven current text-generation models. ModelScope documents its OpenAI-compatible API and a recurring 2,000-call daily allowance, dynamically limited to at most 500 calls per model. |
| SiliconFlow | Added the documented free `Qwen/Qwen3-8B` route. SiliconFlow documents OpenAI-compatible chat completions and fixed free-model limits; free access requires identity verification but not a payment card. |

Result: **22 providers, 253 enabled chat routes, and 394 cataloged chat
models**. The catalog also contains non-chat capabilities such as embeddings,
speech, and image generation, so all-capability totals are higher.

## Candidates not added

- Together AI says it does not offer trials and requires a minimum credit
  purchase, so it is not a free-tier provider for this project.
- Fireworks prices inference per token. Its no-card account limits do not make
  inference free, so it was not added.
- Kilo's newly listed Kat Coder free route returned repeated 429 responses and
  remains unverified. Its content-safety classifier is not a general chat model.
- ModelScope exposes a much larger dynamic catalog; only current, general text
  generation routes were added to avoid presenting every listing as proven
  usable under the recurring free allowance.

## Primary sources

- [Aion pricing](https://www.aionlabs.ai/pricing/), [rate limits](https://www.aionlabs.ai/docs/rate-limits/), [models](https://www.aionlabs.ai/docs/models/), and [API reference](https://api.aionlabs.ai/docs/api-reference/)
- [ModelScope API-Inference documentation](https://modelscope.cn/docs/model-service/API-Inference)
- [SiliconFlow rate limits](https://docs.siliconflow.cn/en/userguide/rate-limits/rate-limit-and-upgradation) and [chat-completions API](https://docs.siliconflow.cn/en/api-reference/chat-completions/chat-completions)
- [Together billing documentation](https://docs.together.ai/docs/billing)
- [Fireworks pricing](https://fireworks.ai/pricing)

Free tiers and model availability change without notice. Treat the packaged
catalog as a maintained routing snapshot, not a guarantee of future capacity.
