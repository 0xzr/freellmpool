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
request has a timeout, and decoded bodies are capped at 4 MB. User catalog
overrides are deliberately ignored.

Unknown and partial listing scopes can identify new candidates, but missing
rows are recorded as unconfirmed absences. They are not retirement evidence.
An empty response, malformed JSON, 429 rate limit, 402 billing/credit response,
provider-wide authentication failure, timeout, and transient 5xx response
never cause a retirement recommendation.

## Keyed probes run locally

Provider keys are never stored in GitHub. The workflow runs only the keyless
public discovery above. Completion probes and catalog refreshes run on a
maintainer machine with that machine's freellmpool key configuration (real
environment variables, then the `[keys]` table in
`~/.config/freellmpool/config.toml`), and the result reaches GitHub only as a
reviewed change to the packaged catalog.

Run keyed probes only on a reviewed checkout of `main`. Each provider's key is
sent to the `base_url` that provider declares in the catalog being probed, so a
checkout with an untrusted catalog edit could send a key to the wrong host.

The local refresh procedure:

1. Run public discovery (below) for listing drift.
2. Run `python3 scripts/vet_catalog.py --report <path>`. It lists each
   configured provider's live models and pings every catalog route, enabled
   and disabled, through the real client path. Leave priced or credit-funded
   providers such as Vercel out with `-p` unless you intend to spend credit.
3. Re-ping candidate changes so each one rests on repeated results. A 401,
   402, 429, timeout, or isolated 5xx is not retirement evidence.
4. Edit `src/freellmpool/providers.toml`, then run
   `python3 scripts/validate_catalog.py`, `scripts/check-counts` (update every
   count claim it reports), `python3 scripts/render_assets.py` on the pinned
   toolchain, and the test suite.
5. Record the dispositions in a dated `docs/MODEL_ACTIVITY_AUDIT_*.md` and open
   a normal pull request.

`scripts/catalog_sentinel.py probe` is a smaller bounded canary over enabled
automatic routes. It reads the same local key configuration and returns only
the catalog-declared key names. Each canary requests at most eight output
tokens and disables the normal client convenience that raises
reasoning-model budgets. The probe report contains provider IDs, catalog model
IDs, HTTP status classifications, timestamps, and lifecycle counters. It
excludes keys, account identifiers, provider response bodies, exception text,
prompts, and completion text.

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

For a local bounded probe with your configured keys, choose explicit bounds.
Pass the preceding report with `--previous` so failure counters and probe
rotation carry across runs. The command prints how many keyed providers it
found local keys for:

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
