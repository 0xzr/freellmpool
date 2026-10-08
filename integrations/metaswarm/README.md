# freellmpool review adapter for metaswarm

This integration lets metaswarm use `freellmpool` as an independent
review/second-opinion tool in an `external-tools` workflow.

The adapter is **review-only**. `freellmpool` routes prompts to model providers;
it does not own a worktree-editing contract, so implementation should stay with
your coding agents while `freellmpool` supplies adversarial review.

## Install

Copy the adapter into your project and make it executable:

```sh
mkdir -p .metaswarm/adapters
cp integrations/metaswarm/freellmpool-review-adapter.sh .metaswarm/adapters/freellmpool.sh
chmod +x .metaswarm/adapters/freellmpool.sh
```

Install `freellmpool` where metaswarm can run it:

```sh
python -m pip install freellmpool
```

The adapter's provider check runs a small Python helper that imports
`freellmpool.config`, so it needs Python 3.11+ with freellmpool importable. The
adapter picks that interpreter in this order:

1. `FREELLMPOOL_PYTHON` (explicit override),
2. the interpreter named in the `freellmpool` script's `#!` line (pip, pipx and
   uv console scripts name the Python they were installed into),
3. a `python3` next to the `freellmpool` executable, after following symlinks,
4. `$VIRTUAL_ENV/bin/python3` when a virtualenv is active,
5. `python3` on `PATH`.

The `freellmpool` executable is `FREELLMPOOL_CMD` when set, otherwise the one on
`PATH`. The adapter's other helpers use only the standard library and run on
`python3` from `PATH`. If the provider check cannot run, `review` fails closed
with `error_type: "helper_unavailable"` and the helper's error in `raw_log`
instead of misreporting provider authentication, and `health` prints a warning.

Configure the strong review providers used by your model list. The default panel
spans Mistral, NVIDIA, and OpenRouter credentials:

```sh
export MISTRAL_API_KEY=...
export NVIDIA_API_KEY=...
export OPENROUTER_API_KEY=...
```

Alternatively use `freellmpool keys add <provider>` and keep the generated key
inventory on the same machine that runs metaswarm. To run with only one provider,
restrict `FREELLMPOOL_STRONG_PROVIDERS` and `FREELLMPOOL_STRONG_MODELS` to that
provider before dispatch.

## Metaswarm config

Add a review adapter entry to `.metaswarm/external-tools.yaml`:

```yaml
adapters:
  freellmpool:
    enabled: true
    roles: ["review", "second_opinion"]
    adapter_path: ".metaswarm/adapters/freellmpool.sh"
    model: "strong-long-context"
    routing: "quality"
    review_mode: "strong"
    strong_providers: ["mistral", "nvidia", "openrouter"]
    strong_models:
      - "nvidia/moonshotai/kimi-k2.6"
      - "nvidia/z-ai/glm-5.1"
      - "nvidia/mistralai/mistral-large-3-675b-instruct-2512"
      - "mistral/mistral-large-latest"
      - "nvidia/nvidia/nemotron-3-ultra-550b-a55b"
      - "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"
      - "openrouter/openai/gpt-oss-120b:free"
    max_models: 7
    max_tokens: 65536
    provider_timeout_seconds: 600
    synthesis_timeout_seconds: 600
    timeout_seconds: 0
    sandbox: none

routing:
  review_order: ["freellmpool"]
  second_opinion: "freellmpool"
```

Both `review` and `second_opinion` route to the adapter's `review` command.
Metaswarm decides when a second opinion is needed; the adapter keeps one
review-only execution path and intentionally rejects `implement`.

The exact model list is intentionally configurable. Free-tier provider health
changes, so override `FREELLMPOOL_STRONG_MODELS` or the YAML value when a model is
slow, unavailable, or no longer useful for review.

## Commands

The adapter implements the metaswarm-style command surface:

```sh
.metaswarm/adapters/freellmpool.sh health

.metaswarm/adapters/freellmpool.sh review \
  --worktree /path/to/worktree \
  --rubric-file /path/to/rubric.md \
  --spec-file /path/to/spec.md \
  --timeout 600
```

Every command returns a JSON envelope with the tool name, command, exit code,
raw log path, and `error_type` when a failure is classified.

When no required strong provider key is configured, or when the strong model list
contains a provider without a configured key, `review` fails closed with
`error_type: "auth_missing"` before making provider calls. The raw log lists the
required environment variable names or provider ids but not secret values.

## Environment

| Variable | Purpose |
| --- | --- |
| `FREELLMPOOL_CMD` | Path to the `freellmpool` executable. |
| `FREELLMPOOL_PYTHON` | Python 3.11+ interpreter for the adapter's provider-check helper (must have freellmpool importable). |
| `FREELLMPOOL_REVIEW_MODE` | `strong` (default), `strong-long-context`, `tokenmax`, or `ask`. |
| `FREELLMPOOL_STRONG_MODELS` | Comma-separated exact provider/model ids for strong mode. |
| `FREELLMPOOL_STRONG_PROVIDERS` | Provider ids that must be configured for ready health. |
| `FREELLMPOOL_MAX_MODELS` | Model cap for strong/tokenmax review. |
| `FREELLMPOOL_MAX_MODELS_HARD_CAP` | Safety cap for concurrent model calls, default `16`. |
| `FREELLMPOOL_MAX_TOKENS` | Max output tokens per model. |
| `FREELLMPOOL_PROVIDER_TIMEOUT_SECONDS` | Per-model upstream timeout. |
| `FREELLMPOOL_SYNTHESIS_TIMEOUT_SECONDS` | Synthesis call timeout. |
| `FREELLMPOOL_ADAPTER_PATH` | Sanitized `PATH` used for child `freellmpool` calls. |
| `METASWARM_LOG_DIR` | Directory for raw review logs. |

Use provider keys from your environment (`MISTRAL_API_KEY`, `NVIDIA_API_KEY`,
`OPENROUTER_API_KEY`) or from `freellmpool keys add`.
