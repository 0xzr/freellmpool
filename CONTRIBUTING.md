# Contributing to freellmpool

Thanks for helping! The two highest-value contributions are **adding free
providers** and **keeping the existing catalog accurate** as free tiers drift.

## Dev setup

```bash
git clone https://github.com/0xzr/freellmpool
cd freellmpool
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
ruff check .
pytest          # 0 network calls; provider traffic is faked
```

Focused loop while you are working:

```bash
ruff check .
pytest tests/test_cli.py              # or the smallest relevant test file
scripts/check-counts                  # when README/docs count claims change
python3 scripts/validate_catalog.py   # when providers.toml changes
```

Useful release-readiness checks:

```bash
python3 scripts/stress_proxy.py --profile ci
python3 scripts/check_release_ready.py --skip-build
python3 scripts/check_release_ready.py
```

`stress_proxy.py` starts a local fake-backed proxy and sends mixed chat,
streaming, embeddings, Responses, Anthropic Messages, transcription, models,
and health traffic through the real HTTP server. `check_release_ready.py`
cross-checks version/provider/model-count metadata; without `--skip-build` it
also builds the sdist/wheel, runs `twine check`, and fresh-installs the wheel.

Good first issue drafts live in [`docs/GOOD_FIRST_ISSUES.md`](docs/GOOD_FIRST_ISSUES.md).
They include context, pointers, acceptance checks, labels, and the exact
maintainer commands for filing them.

## Adding a provider

The whole catalog is [`src/freellmpool/providers.toml`](src/freellmpool/providers.toml).
The scheduled discovery and protected-probe contract is documented in
[`docs/CATALOG_SENTINEL.md`](docs/CATALOG_SENTINEL.md); sentinel output is
advisory and never authorizes an automatic catalog mutation.
Most providers are OpenAI-compatible, so adding one is just a TOML block:

```toml
[[provider]]
id = "myprovider"
label = "My Provider"
adapter = "openai"                       # "openai" | "gemini" | "cloudflare"
base_url = "https://api.myprovider.ai/v1"
key_env = "MYPROVIDER_API_KEY"           # env var the user sets; never a key
models = [
    { name = "some-model", rpd = 0 },    # rpd = free daily request hint, 0 = unknown
]
```

Rules of thumb:

- **Free tier only.** freellmpool is about free pools. If a provider needs a card
  on file to use the tier, it doesn't belong in the default catalog.
- **Never commit a key.** Only the *name* of the env var goes in the catalog.
- If the provider isn't OpenAI-compatible, it needs a small adapter in
  [`src/freellmpool/client.py`](src/freellmpool/client.py) (see the `gemini` one for
  a ~30-line template) and a unit test in `tests/`.
- Add the env var to [`.env.example`](.env.example) and the signup steps to
  [`docs/ACCOUNTS.md`](docs/ACCOUNTS.md).

## Fixing a stale limit or endpoint

Free tiers change constantly. If a model name, base URL, or daily limit is
wrong, a one-line PR to `providers.toml` is perfect and very welcome.

## Tests

Every code path is unit-tested without touching the network via an injected
fake transport (`tests/helpers.py`). Please keep it that way — new behavior
should come with a fake-backed test. Run `pytest` and `ruff check` before
opening a PR.

## Code of conduct

Be kind. Assume good faith. We're all here to make free LLMs easier to use.
