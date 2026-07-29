# Catalog sentinel operations

The `catalog-sentinel` workflow is a weekly and manually dispatchable,
advisory drift detector. It produces a bounded JSON workflow artifact and a
short Markdown artifact. When a public listing contains actionable additions
or an authoritative removal, the workflow opens or comments on a single
maintainer-review issue.

It never enables or disables a route, edits `providers.toml`, purchases
credits, or changes runtime routing. A maintainer must reproduce the evidence,
check provider terms and billing behavior, run completion probes, and submit a
normal reviewed pull request before catalog state changes.

## Public discovery

The public job sends unauthenticated `GET` requests only to model-list
endpoints derived from the packaged catalog. Redirects are disabled, each
request has a timeout, and decoded bodies are capped at 1 MB. User catalog
overrides are deliberately ignored.

Unknown and partial listing scopes can identify new candidates, but missing
rows are recorded as unconfirmed absences. They are not retirement evidence.
An empty response, malformed JSON, 429 rate limit, 402 billing/credit response,
provider-wide authentication failure, timeout, and transient 5xx response
never cause a retirement recommendation.

## Protected completion probes

Authenticated canaries run in the GitHub `catalog-sentinel` environment. Turn
on environment protection and require a maintainer reviewer before configuring
the environment secret:

```text
FREELLMPOOL_SENTINEL_KEYS_JSON
```

Its value is a bounded JSON object that maps the catalog's environment-variable
names to their values. For example, configure it through GitHub's encrypted
environment-secret UI; never commit the value:

```json
{"GROQ_API_KEY":"...","CLOUDFLARE_API_TOKEN":"...","CLOUDFLARE_ACCOUNT_ID":"..."}
```

The probe report contains provider IDs, catalog model IDs, HTTP status
classifications, timestamps, and lifecycle counters. It excludes keys,
account identifiers, provider response bodies, exception text, prompts, and
completion text. If the secret is absent, the protected job records that probes
were skipped without weakening public discovery.

Each canary requests at most eight output tokens and explicitly disables the
normal client convenience that raises reasoning-model budgets. Provider count,
models per provider, request timeout, and the overall protected job are all
bounded independently.

## Lifecycle and artifacts

Pinned cache actions restore the preceding sanitized report when available.
The sentinel carries forward only validated timestamps and bounded counters for
matching packaged provider/model identities. Invalid, oversized, stale-schema,
or missing state is ignored.

Each successful run uploads its current JSON and Markdown workflow artifact
with 30-day retention. Cache loss or artifact expiry resets counters but cannot
change routing. Treat the artifact and generated issue as leads—not proof that
a model is free, healthy, or retired.

For a local public run:

```bash
python3 scripts/catalog_sentinel.py discover \
  --output /tmp/catalog-sentinel.json \
  --summary /tmp/catalog-sentinel.md
```

For a local protected probe, export the JSON secret map and choose explicit
bounds:

```bash
python3 scripts/catalog_sentinel.py probe \
  --output /tmp/catalog-probes.json \
  --summary /tmp/catalog-probes.md \
  --max-providers 8 \
  --max-models-per-provider 1
```

Inspect the workflow artifact, reproduce any candidate with
`scripts/vet_catalog.py`, and follow the catalog rules in
[`CONTRIBUTING.md`](../CONTRIBUTING.md).
