# Frontier free-provider audit — 2026-07-19

## Goal and admission standard

This audit looks for additional programmatic inference sources for the strongest
current open-weight models, with extra emphasis on GLM-5.2. A source counts as
useful capacity only when it offers a documented API and either recurring free
allowance or a clearly labelled trial. Browser-only chat sites, reverse-engineered
sessions, temporary launch promotions, paid compute for self-hosting, and claims
that cannot be confirmed from an official source are excluded.

The search covered official provider model/pricing pages, provider APIs where an
account was already configured, Artificial Analysis' GLM-5.2 provider roster,
GitHub free-API lists, coding-credit lists, and related gateway projects.

## Executive findings

1. **Sail Research is the strongest genuinely new recurring source found.** It
   documents $5 of free credit every month and the exact model
   `zai-org/GLM-5.2-FP8` behind an OpenAI-compatible endpoint. It needs an
   account/key and a live validation before integration.
2. **AnyAPI.ai is a high-confidence recurring candidate**, with 100K AnyTokens
   per day. Its inventory labels GLM-5.2 as “Basic,” and its $0 plan includes
   all free and Basic models. The public models endpoint requires authentication,
   so the exact model ID and actual free-plan completion still need validation.
3. **Ollama Cloud's model list is a false positive for the current free plan.**
   The configured account's `/v1/models` response advertises `glm-5.2`,
   `kimi-k2.7-code`, `deepseek-v4-pro`, and `deepseek-v4-flash`, but a minimal
   completion against every one returned HTTP 403 “requires a subscription.”
   MiniMax-M3 instead returned a free-session usage-limit response, which is
   consistent with it being the only usable free frontier route already cataloged.
4. **Tencent TokenHub and Scaleway are valuable GLM-5.2 trials, not recurring
   free capacity.** Each documents one million free tokens for new accounts;
   Tencent's quota expires after 90 days.
5. Most additional Artificial Analysis inference vendors are paid-only. Their
   presence in a benchmark roster is not evidence of a free API tier.

## Confirmed or high-confidence recurring GLM-5.2 sources

| Provider | Exact model | Free allowance | Status in freellmpool | Confidence / caveat |
|---|---|---|---|---|
| NVIDIA NIM | `z-ai/glm-5.2` | Free developer API endpoint | Configured and cataloged | Existing source |
| Hugging Face Inference Providers | `zai-org/GLM-5.2` | Small monthly included inference credit | Configured and cataloged | Recurring but low capacity; launch-period promotions should not be treated as permanent |
| ModelScope | `ZhipuAI/GLM-5.2` | 2,000 calls/day account-wide, 200 calls/day/model | Cataloged; token missing | Strong recurring source once configured |
| Morph | `morph-glm52-744b` | 200 requests/month | Cataloged; token missing | Small but recurring |
| Vercel AI Gateway | `zai/glm-5.2` | $5/month Hobby credit | Cataloged; token missing | Credit is shared with other gateway use |
| Sail Research | `zai-org/GLM-5.2-FP8` | $5 credit/month | Not integrated; token missing | Official OpenAI-compatible API. Terms say the service is for business/enterprise internal use and responses may be stored; review fit before enabling |
| AnyAPI.ai | Model ID unknown until authenticated discovery | 100K AnyTokens/day on free plan | Not integrated; token missing | Official inventory labels GLM-5.2 “Basic,” which the $0 plan includes. Still require a live free-account request before enabling |

Official references:

- [Ollama GLM-5.2 model page](https://ollama.com/library/glm-5.2)
- [NVIDIA GLM-5.2 endpoint](https://build.nvidia.com/z-ai/glm-5.2)
- [Sail Research models and monthly credit](https://www.sailresearch.com/)
- [Sail terms](https://sail.systems/terms)
- [AnyAPI model inventory](https://anyapi.ai/)
- [AnyAPI pricing](https://anyapi.ai/pricing)

## Trial-only GLM-5.2 sources

| Provider | Exact model | Trial | Integration note |
|---|---|---|---|
| Tencent Cloud TokenHub | `glm-5.2` | 1M tokens for a new primary account, valid 90 days | OpenAI-compatible base URL `https://tokenhub.tencentmaas.com/v1`; useful burst capacity, but not a permanent pool member unless the product later gains a recurring tier |
| Scaleway Generative APIs | `glm-5.2` | First 1M tokens for each new customer | One-time customer allowance, then metered billing |
| Fireworks | `accounts/fireworks/models/glm-5p2` | $1 signup credit | Too small and non-recurring for normal pool positioning |
| Baseten | `zai-org/GLM-5.2` | Historically $30 new-workspace credit | Verify the current signup offer before relying on it; non-recurring |
| Alibaba Cloud Model Studio | GLM-5.2 listed in regional catalog | New-account quotas generally expire after 90 days | Exact regional quota and endpoint require console validation |
| Novita | GLM-5.2 offered | Small signup credit reported | Treat as unconfirmed until current official account terms and model access are checked |

References:

- [Tencent TokenHub free trial](https://cloud.tencent.com/document/product/1823/130053)
- [Tencent TokenHub API model IDs](https://cloud.tencent.com/document/product/1823/130078)
- [Scaleway Generative APIs](https://www.scaleway.com/en/generative-apis/)
- [Fireworks pricing](https://fireworks.ai/pricing)
- [Fireworks GLM-5.2 announcement](https://fireworks.ai/blog/glm-5p2)
- [Baseten GLM-5.2 availability](https://www.baseten.co/resources/changelog/glm-52-available-on-baseten/)
- [Alibaba Model Studio pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing)

## Sources checked and rejected or deferred

| Source | Decision | Reason |
|---|---|---|
| Ollama Cloud | Reject on the current free plan | Model discovery lists GLM-5.2, Kimi K2.7 Code, and DeepSeek V4, but live completions return HTTP 403 “requires a subscription.” Keep the existing free MiniMax-M3 route only |
| Cloudflare Workers AI | Reject as free GLM-5.2 capacity | A live call returned HTTP 403 and required Workers Paid. GitHub lists calling it free are stale or over-broad |
| Z.ai direct production API | Reject for this pool goal | GLM-5.2 is metered; the vendor's free API models are lower-tier GLM Flash variants |
| ZenMux `z-ai/glm-5.2-free` | Reject | Limited launch promotion ended; current GLM-5.2 page is paid. Do not encode a stale `-free` route |
| Together AI | Reject | Official support documentation says the free trial was removed and a minimum credit purchase is required |
| Wafer, FriendliAI, Makora, Parasail, CoreWeave, DeepInfra, Nebius, Blackbox, Databricks, SiliconFlow | Defer/reject for GLM-5.2 | These appear in benchmark or model-hosting inventories, but no durable recurring free GLM-5.2 API allowance was verified |
| GMI Cloud | Defer | Promotional credit appears to require adding a payment method; no durable recurring tier confirmed |
| Nebius Builders | Defer | Program/preview credit is application-based, not a general recurring free tier |
| iFlytek Astron MaaS | Defer | `xopglm52` exists, but documentation points to a paid Token Plan rather than a free API allocation |
| CSDN/AtomGit promotions | Reject for routing | Daily lotteries and limited claim windows are not dependable server-side capacity |
| Puter.js and free chat websites | Reject | Browser-mediated access is not a stable provider API suitable for freellmpool |
| Self-hosted GLM-5.2 weights | Out of scope | The weights are open, but the hardware is not free and the deployment is too large to count as a vendor free tier |

Artificial Analysis currently measures GLM-5.2 across vendors including Wafer,
FriendliAI, Makora, Novita, GMI, Parasail, CoreWeave, Baseten, Together,
DeepInfra, Nebius, Blackbox, Databricks, Scaleway, Fireworks, and SiliconFlow.
That roster was used for discovery only; each provider still had to pass the
free-allowance test above.

References:

- [Artificial Analysis GLM-5.2 benchmark roster](https://artificialanalysis.ai/models/glm-5-2/providers)
- [Together AI free-tier change](https://support.together.ai/articles/1862638756-changes-to-free-tier-and-billing-july-2025)
- [ZenMux GLM-5.2](https://zenmux.ai/z-ai/glm-5.2)
- [Blackbox GLM-5.2](https://www.blackbox.ai/models/blackboxai/z-ai/glm-5.2)

## GitHub and community discovery sources surveyed

- [mnfst/awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis)
- [cheahjs/free-llm-api-resources](https://github.com/cheahjs/free-llm-api-resources)
- [open-free-llm-api/awesome-freellm-apis](https://github.com/open-free-llm-api/awesome-freellm-apis)
- [12britz/awesome-free-models](https://github.com/12britz/awesome-free-models)
- [codertesla/ai-coding-deals](https://github.com/codertesla/ai-coding-deals)
- [raffiihza/free-llm-coding-credits](https://github.com/raffiihza/free-llm-coding-credits)
- [EthanThatOneKid/free-inference-skills](https://github.com/EthanThatOneKid/free-inference-skills)

These lists are useful leads, but several still repeat obsolete claims about
Cloudflare, Together, DeepInfra, or temporary `-free` model routes. Official
pricing documentation and live endpoint behavior take precedence.

## Next actions and credentials

1. Create a Sail account and provide `SAIL_API_KEY`; validate `/v1/models`, chat
   completions, streaming, tools, rate-limit headers, and monthly-credit behavior.
2. Create an AnyAPI account and provide `ANYAPI_API_KEY` for validation. Add it
   only if GLM-5.2 appears in authenticated discovery and a $0-plan call succeeds.
3. Do not add Ollama's subscription-only frontier model IDs merely because they
   appear in `/v1/models`; the completion smoke test is the admission gate.
4. Existing high-value keys still worth adding are `MODELSCOPE_API_TOKEN`,
   `MORPH_API_KEY`, and `AI_GATEWAY_API_KEY` (Vercel).
5. If one-time burst capacity is useful, create Tencent TokenHub and Scaleway
   credentials. Keep them labelled `trial`, so routing and documentation do not
   imply recurring capacity.

This note is a point-in-time audit. Free-tier terms and model inventories change
quickly; every new integration should retain an official source URL and pass a
live authenticated smoke test before being enabled by default.
