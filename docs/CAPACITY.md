# Capacity management

freellmpool can pool several legitimate LLM providers behind one local endpoint. The capacity tools help you see what is usable right now, what is close to quota, and what needs manual setup.

This feature is intentionally read-only. It does not create accounts, does not manage email inboxes, and does not try to bypass provider limits.

## Main commands

```bash
freellmpool capacity status --target 5
freellmpool keys status --target 5
freellmpool keys checklist --target 5
freellmpool providers health
freellmpool benchmark
freellmpool doctor
```

## Provider capacity

`capacity status` summarizes the local catalog, configured environment variables, daily quota counters and optional key inventory.

Statuses:

- `healthy`: configured and usable according to local state.
- `low_quota`: usage is above 80 percent of the daily request hint.
- `exhausted`: usage reached the daily request hint.
- `invalid_key`: local inventory says the key has expired.
- `missing`: the provider exists in the catalog but is not configured.
- `disabled`: the provider currently has no enabled models, so it is not usable
  capacity even if it is keyless or configured.

Example:

```text
LLM capacity: 3/5 healthy providers
Action recommended: add 2 provider(s).
```

## Advisory external catalog

The external provider catalog is discovery metadata only. Syncing it downloads a
sanitized public list into the local cache; it does not modify the packaged
`providers.toml`, add providers to automatic routing, or call provider inference
APIs.

```bash
freellmpool catalog sync
freellmpool catalog status
```

By default the cache is stored at
`~/.config/freellmpool/provider_catalog.json`. `catalog status` reads that cache
and shows the available suggestions. A provider becomes usable only after you
explicitly import or define it and configure any required credentials.

## Key inventory

The key inventory is optional. It tracks metadata about keys you created manually. By default it is read from:

```text
~/.config/freellmpool/keys.toml
```

You can override it with:

```bash
export FREELLMPOOL_KEYS_PATH=/path/to/keys.toml
```

Example:

```toml
[[keys]]
provider = "groq"
env_var = "GROQ_API_KEY"
label = "personal-main"
created_at = "2026-06-05"
expires_at = "2026-12-31"
commercial_allowed = true
notes = "created manually"
```

The inventory should not contain raw API key values. Keep real secrets in environment variables, `config.toml`, your shell profile, or a secret manager.

## Manual checklist

`keys checklist --target 5` tells you which providers to configure manually to reach the desired number of healthy providers.

Example:

```text
Manual key checklist to reach 5 healthy providers:
  - gemini: create a key manually, then set GEMINI_API_KEY
  - cloudflare: create a key manually, then set CLOUDFLARE_API_TOKEN
```

## Adding a provider key

`keys add` first looks for an existing local provider. If the provider is not local, it checks the synced external catalog. Typos and model names are matched with a small Levenshtein search, then the CLI asks before importing the suggested provider.

```bash
freellmpool keys add Hyperbolic
freellmpool keys add Hyperbolc
freellmpool keys add Llama-3.3-70B-Instruct
```

If no external match is good enough, the CLI can create a minimal OpenAI-compatible provider in the user `providers.toml`. It asks for the API base URL and a default model id. Leave the model blank to autodiscover models from the OpenAI-compatible `GET /models` endpoint; if a key is needed, the CLI uses the key passed with `--value` or asks for one.

Non-interactive example:

```bash
freellmpool keys add Hyperbolic \
  --base-url https://api.hyperbolic.xyz/v1 \
  --model meta-llama/Llama-3.3-70B-Instruct \
  --value "$HYPERBOLIC_API_KEY" \
  --yes
```

## Provider health

`providers health` sends one tiny request to each configured provider and reports latency or failure.

```bash
freellmpool providers health
freellmpool providers health --timeout 10
freellmpool providers health -p groq,gemini
freellmpool providers health -m openai/gpt-oss-120b
```

This is different from `capacity status`. `providers health` sends real test requests to each configured provider's API. `capacity status` never calls a provider, but by default it does refresh the advisory external catalog over the network (a read-only metadata fetch); pass `--no-catalog-sync` to keep it fully local.

`benchmark` uses the same real-provider path but reports the timing table used
to warm latency-aware routing. Run it before long agent sessions when you want
`FREELLMPOOL_ROUTING=fast` to start with fresh latency information.

`doctor` is a local diagnostic command. It does not call provider APIs; it checks
version/config/quota/cache/catalog state and exits non-zero when catalog
validation fails. If you are deliberately testing a local HTTP provider, opt in
with `FREELLMPOOL_ALLOW_LOCAL_PROVIDERS=1` for runtime calls, but expect catalog
validation to keep requiring HTTPS-safe provider metadata.

## Dashboard

When the proxy is running, open:

```text
http://127.0.0.1:8080/dashboard
```

The dashboard shows request counters, cache hits, estimated savings, provider usage, capacity status, and measured latency if the process has already made calls or health checks.

## Recommended workflow

1. Run `freellmpool capacity status --target 5`.
2. Run `freellmpool keys checklist --target 5`.
3. Add provider keys manually.
4. Run `freellmpool providers health`.
5. Optionally run `freellmpool benchmark` to seed latency-aware routing.
6. Start the proxy and watch `/dashboard`.

## Limits

The daily quota hints are local estimates. Providers can change limits or return 429 earlier. The router still reacts to real provider failures at request time.
