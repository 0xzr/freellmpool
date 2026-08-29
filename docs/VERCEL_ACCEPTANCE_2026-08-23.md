# Vercel AI Gateway acceptance audit — 2026-08-23

Status: **incomplete; issue #68 remains open**. Updated through 2026-08-29.
Public catalog, zero-price filtering, and billing-error controls are verified,
but Vercel currently requires customer verification and a valid card on file
for this Hobby account. Repeat live completion, returned serving-provider
provenance, and actual response cost are not yet proven.

## Routing policy

The maintainer chose key-only activation on 2026-08-23: setting
`AI_GATEWAY_API_KEY` adds Vercel's eligible zero-price capacity to automatic
routing. As of 2026-08-29, that set contains only
`poolside/laguna-s-2.1-free`. Priced DeepSeek V4 Flash and unsuffixed Nemotron
3.5 Lightning remain enabled only as explicit pins. The package does not buy
credits or change dashboard settings, but it cannot enforce the account's
auto-top-up state. A Vercel-side API-key budget and disabled auto-top-up remain
operator requirements for any priced pin.

## Public model and endpoint evidence

`python3 scripts/verify_vercel_gateway.py --public-only` passed again on
2026-08-29 after the automatic set was tightened.
It fetched the anonymous `/v1/models` listing and each model's
`/v1/models/{creator}/{model}/endpoints` listing with redirects disabled and a
1 MB response cap.

| Automatic model | Context | Max output | Aggregate input / 1M | Aggregate output / 1M | Active endpoints | Endpoint evidence |
|---|---:|---:|---:|---:|---:|---|
| `poolside/laguna-s-2.1-free` | 256,000 | 32,768 | $0 | $0 | 1 | Poolside; every advertised price field is zero |

The verifier found exactly that one catalog row and one active Poolside endpoint;
all aggregate and endpoint price fields were numeric zero. Current nonautomatic
dispositions are:

| Model or candidate | Disposition | Reason |
|---|---|---|
| `nvidia/nemotron-3.5-lightning-free` | Disabled lifecycle record | Absent from the live listing; the unsuffixed ID is separately priced, not a rename |
| `nvidia/nemotron-3.5-lightning` | Enabled, `auto = false` | Priced explicit pin |
| `deepseek/deepseek-v4-flash-0731` | Enabled, `auto = false` | Priced explicit pin |
| `inclusionai/ling-3.0-flash-fin` and `inclusionai/ling-3.0-flash-fin-free` | Disabled, `auto = false` | Pending completion, provenance, and cost canaries |
| `minimax/minimax-m2.7-free` and `minimax/minimax-m3-free` | Disabled, `auto = false` | Pending completion, provenance, and cost canaries |

The gateway can dynamically choose among active endpoints for priced routes, so
an aggregate price is not a hard per-request maximum. A local `rpd` value is a
routing-capacity hint, not a spending cap. Current pricing must be rechecked
before relying on any explicit priced pin.

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
zero gateway cost. The serving winner is read from the documented
`providerMetadata.gateway.routing.finalProvider` field and must match one of
the model's active providers from the audited endpoint listing. Missing or
unrecognized metadata and nonzero cost fail closed. Priced-model canaries
require `--attest-credit-spend`.

Every Vercel HTTP 402 fails closed as provider-wide account exhaustion and
backs off the account rather than trying another Vercel model. This intentionally
uses the status code as the durable budget signal even if the response body is
empty or changes shape. Generic capability/payment 402 responses from
non-Vercel providers remain model-local. Tests cover 401/403 authentication,
customer verification, 402 billing, 429 rate limits, malformed/empty discovery,
bad pricing, empty/malformed completions, provenance/cost drift, response
bounds, and secret/content redaction.

## Terms and privacy

Requests are processed by Vercel and the selected upstream provider. Review
[Vercel's AI Product Terms](https://vercel.com/legal/ai-product-terms),
[AI Gateway pricing](https://vercel.com/docs/ai-gateway/pricing), and each
model page's linked upstream terms and privacy policy before sending data:

- [Laguna S 2.1 Free](https://vercel.com/ai-gateway/models/laguna-s-2.1-free)
- [Nemotron 3.5 Lightning](https://vercel.com/ai-gateway/models/nemotron-3.5-lightning)
- [DeepSeek V4 Flash 0731](https://vercel.com/ai-gateway/models/deepseek-v4-flash-0731)

Do not send secrets or regulated/confidential content unless both Vercel's and
the actual serving provider's terms meet the workload's requirements.
