# Protocol conformance canaries

A successful short completion proves basic availability, not support for
streaming, tools, structured JSON, images, the OpenAI Responses bridge, or the
Anthropic Messages bridge. freellmpool records those capabilities separately
for each exact provider/model target.

## Run and inspect canaries

Run the default deterministic matrix against at most the first enabled model
of eight configured providers:

```bash
freellmpool conformance run
freellmpool conformance status
freellmpool conformance status --json
```

Narrow the quota spend when needed:

```bash
freellmpool conformance run \
  --providers groq,cerebras \
  --model some-exact-model \
  --features chat,streaming,tools,json,json_schema,vision,responses,anthropic_messages \
  --max-targets 2 \
  --timeout 10 \
  --json
```

Each selected feature makes at most one request per target, requests no more
than 16 output tokens, and clamps its timeout to 60 seconds. The prompts,
tool schema, and one-pixel vision image are fixed synthetic constants. Canaries
never send repository files, user prompts, or previous conversations.

Registered providers and `freellmpool.providers` entry-point providers are
included in both `freellmpool models` and `freellmpool conformance run`, so
plugin targets can earn the same per-feature evidence as built-in targets.

The state defaults to
`~/.config/freellmpool/conformance.json`. Set
`FREELLMPOOL_CONFORMANCE_FILE` to use another path. The file contains only a
target fingerprint, bounded status/classification values, timestamps, and
verification counts. It never stores credentials, provider response text,
exception messages, prompts, repository content, or user content.

## Routing contract

Feature-specific, unpinned traffic is eligible only for targets with a current
`pass` for every required feature. If no target qualifies, routing fails
locally without spending provider quota. A request that is explicitly pinned
to exactly one provider and one model is the deliberate override.

Evidence is bound to the provider id, adapter, base URL, and model id. Changing
an adapter, endpoint, or model id invalidates the old evidence automatically;
run the canaries again. Unsupported feature results remain separate from
provider availability health and do not open availability circuits.

`freellmpool models --json`, the proxy `/v1/models` responses, and `/status`
include `capabilities` and `verified_features`.

## Protected automation

The scheduled and manually dispatchable catalog-sentinel workflow runs a
bounded matrix inside the protected `catalog-sentinel` environment. It caches
the sanitized state and uploads sanitized run/state artifacts.

The workflow maps its existing protected JSON secret into
`FREELLMPOOL_CONFORMANCE_KEYS_JSON`. This input is capped at 64 KiB; only
catalog-declared `key_env` and `extra_env` names are imported, values are
bounded strings, unknown names are ignored, and values are never printed or
persisted in evidence. This variable is intended for protected automation;
normal local use should continue to use ordinary provider environment
variables or the freellmpool key configuration.
