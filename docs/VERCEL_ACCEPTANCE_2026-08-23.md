# Vercel AI Gateway acceptance audit — 2026-08-23

Status: **incomplete; issue #68 remains open**. Public catalog and billing-error
controls are verified, but Vercel currently requires customer verification and
a valid card on file for this Hobby account, so repeat live completion, returned serving-provider
provenance, and actual response cost are not yet proven.

## Routing policy

The maintainer explicitly chose key-only activation on 2026-08-23: setting
`AI_GATEWAY_API_KEY` intentionally adds Vercel to automatic routing. This
supersedes issue #68's earlier key-plus-opt-in/no-silent-credit-use policy.
DeepSeek V4 Flash consumes the included credit when selected. The package does
not buy credits or change dashboard settings, but it cannot enforce the account's
auto-top-up state. A Vercel-side API-key budget and disabled auto-top-up remain
operator requirements.

Automatic routing contains three value routes. The five existing frontier
routes remain enabled and pinnable, but `auto = false` keeps them out of
automatic fan-out.

## Public model and endpoint evidence

`python3 scripts/verify_vercel_gateway.py --public-only` passed on 2026-08-23.
It fetched the anonymous `/v1/models` listing and each model's
`/v1/models/{creator}/{model}/endpoints` listing with redirects disabled and a
1 MB response cap.

| Automatic model | Context | Max output | Aggregate input / 1M | Aggregate output / 1M | Active endpoints | Endpoint evidence |
|---|---:|---:|---:|---:|---:|---|
| `poolside/laguna-s-2.1-free` | 256,000 | 32,768 | $0 | $0 | 1 | Poolside; every advertised price field is zero |
| `nvidia/nemotron-3.5-lightning-free` | 1,000,000 | 32,768 | $0 | $0 | 1 | NVIDIA; every advertised price field is zero |
| `deepseek/deepseek-v4-flash-0731` | 1,000,000 | 384,000 | $0.076 | $0.153 | 10 | Active endpoints ranged up to $0.28/M prompt and $0.66/M completion |

The gateway dynamically chooses among active endpoints, so DeepSeek's aggregate
price is not a hard per-request maximum. `rpd = 50` is a routing capacity hint,
not a spending cap. Current pricing must be rechecked before relying on it.

## Credentialed evidence

The supplied bearer key is stored locally with owner-only permissions. Three
bounded calls through `freellmpool.client.call` used a zero-priced model,
`max_tokens = 8`, and disabled the reasoning-token floor. All three failed with
HTTP 403 and the allowlisted classification `customer_verification_required`;
the sanitized account-facing reason requires a valid card on file.
No completion was returned and this is not acceptance evidence.

After Vercel clears the account requirement, the verifier must produce three
non-empty normal-client completions. Successful zero-price acceptance also
requires the exact returned model, serving-provider provenance, and a numeric
zero gateway cost. Missing metadata or nonzero cost fails closed. Priced-model
canaries require `--attest-credit-spend`.

HTTP 402 is classified as billing/credit exhaustion and backs off the Vercel
account rather than retiring a model. Tests cover 401/403 authentication,
customer verification, 402 billing, 429 rate limits, malformed/empty discovery,
bad pricing, empty/malformed completions, provenance/cost drift, response bounds,
and secret/content redaction.

## Terms and privacy

Requests are processed by Vercel and the selected upstream provider. Review
[Vercel's AI Product Terms](https://vercel.com/legal/ai-product-terms),
[AI Gateway pricing](https://vercel.com/docs/ai-gateway/pricing), and each
model page's linked upstream terms and privacy policy before sending data:

- [Laguna S 2.1 Free](https://vercel.com/ai-gateway/models/laguna-s-2.1-free)
- [Nemotron 3.5 Lightning Free](https://vercel.com/ai-gateway/models/nemotron-3.5-lightning-free)
- [DeepSeek V4 Flash 0731](https://vercel.com/ai-gateway/models/deepseek-v4-flash-0731)

Do not send secrets or regulated/confidential content unless both Vercel's and
the actual serving provider's terms meet the workload's requirements.
