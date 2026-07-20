# Integrations

`freellmpool` exposes a standard **OpenAI-compatible** API, so any tool that lets
you set a custom base URL can run on pooled free inference. Start the gateway
once, then point your tool at it:

```bash
freellmpool proxy --port 8080
# Base URL:  http://localhost:8080/v1
# API key:   anything on loopback, or your proxy bearer token when auth is enabled
# Model:     auto        (or "groq", or "groq/llama-3.3-70b-versatile")
```

Because freellmpool **aliases common model names** (`gpt-4o-mini`, `gpt-4o`,
`claude-3-5-sonnet`, …) to free models, most tools work with their *default*
model setting untouched — just set the base URL and any API key.

---

## Coding agents & editors

Profiles provide the same setup snippets through the CLI:

```bash
freellmpool profile list
freellmpool profile show opencode
freellmpool profile install opencode
freellmpool profile doctor opencode --dry-run
```

`profile install` is print-only: it writes the quick-start and config snippets
to stdout so you can inspect or paste them yourself. `profile doctor --dry-run`
prints the checks it would perform without calling binaries or network URLs.

The init wizard detects provider keys, agent CLIs, proxy config, and Tailscale
state, then prints copy-pastable setup plans without editing third-party config:

```bash
freellmpool init --yes
freellmpool init --yes --agent opencode
freellmpool init --yes --agent metaswarm --tailnet
freellmpool init --json
```

`freellmpool code <agent>` remains as a compatibility shortcut for the profile
quick-start.

For long-running agent loops, start with quota-wise mode:

```bash
FREELLMPOOL_MODE=wise freellmpool ask --role conserve "summarize this patch"
freellmpool quota-wise status
```

Wise mode lowers default output size, prefers spread routing, narrows broad
fan-out to declared local quota headroom when possible, and requires `--yes`
before expensive `tokenmax` runs in non-interactive scripts. Per-command
`--mode normal|wise` overrides the environment.

The proxy exposes more than chat completions. Agent integrations can use
`/v1/responses` for Codex-style clients, `/v1/messages` for the experimental
Anthropic bridge, `/v1/models` for concrete `provider/model` ids, `/dashboard`
for operations, `/playground` for browser-side battle runs, and
`/freellmpool/battle` for JSON/Markdown comparison results.

For automation, `/healthz` and `/livez` are public liveness aliases;
`/readyz` is a public advisory local-capacity probe (200 or 503), and
`/v1/providers` is the authenticated secret-free inventory. Use
`/v1/models?ready=true` to keep the normal OpenAI/Anthropic model-list shape
while filtering out locally exhausted or cooling-down targets. These are local
snapshots and never live-probe an upstream provider.

### opencode
`opencode.json` (project or `~/.config/opencode/`):
```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "freellmpool/auto",
  "provider": {
    "freellmpool": {
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://localhost:8080/v1" },
      "models": { "auto": {}, "fast": {}, "quality": {}, "fair": {} }
    }
  }
}
```
Pick `freellmpool/auto|fast|quality|fair` in the model picker to control routing
(`quality` = capability-matched + latency-aware; `fast` = lowest latency; `fair` =
spread quota). Full guide: <https://0xzr.github.io/freellmpool/run-opencode-on-free-models.html>.

**Embedded dashboard + tools (optional).** Two OpenCode plugins live in the repo:
- [`integrations/opencode-tui`](../integrations/opencode-tui) — a live in-editor TUI
  dashboard (routing mode, estimated savings, tokens served free, provider race, latency
  sparkline, last-served model). Install: `opencode plugin -g file:<repo>/integrations/opencode-tui`.
- [`integrations/opencode`](../integrations/opencode) — a server plugin adding
  `freellmpool_status` and `freellmpool_models` tools and a served-model toast.

**Registry publication status: pending.** The intended package names are
`opencode-freellmpool` and `opencode-freellmpool-tui`; CI packs, clean-installs,
and loads both, but do not use an npm install command until the registry versions
are verified. The local-file commands above are the current working path.

### metaswarm
[`integrations/metaswarm`](../integrations/metaswarm) contains an experimental
review-only adapter for metaswarm `external-tools`. It lets metaswarm call
`freellmpool` as an adversarial reviewer or second opinion and returns the same
JSON envelope style as other metaswarm adapters.

Copy the adapter into your project:

```bash
mkdir -p .metaswarm/adapters
cp integrations/metaswarm/freellmpool-review-adapter.sh .metaswarm/adapters/freellmpool.sh
chmod +x .metaswarm/adapters/freellmpool.sh
```

Then add it to `.metaswarm/external-tools.yaml` as a `review` /
`second_opinion` adapter. Configure at least one strong review provider
(`MISTRAL_API_KEY`, `NVIDIA_API_KEY`, or `OPENROUTER_API_KEY`) before enabling it;
without those keys it fails closed with `error_type: "auth_missing"` and makes no
provider calls. Full setup: [`integrations/metaswarm/README.md`](../integrations/metaswarm/README.md).

The installable profile is:

```bash
freellmpool profile show metaswarm
freellmpool profile install metaswarm
freellmpool profile doctor metaswarm --dry-run
```

It includes Tailnet setup for remote agents, a free/cheap worker lane, a larger
freellmpool reviewer lane, and Codex/Opus lanes only as explicit user-owned paid
escalation/final-review tools.

### Hermes Agent

Hermes supports OpenAI-compatible custom endpoints. Start the proxy, then either
run `hermes model` and choose **Custom endpoint**, or inspect the print-only
profile:

```bash
freellmpool profile install hermes
freellmpool profile doctor hermes --dry-run
```

The rendered `~/.hermes/config.yaml` block is:

```yaml
model:
  provider: custom
  default: quality
  base_url: http://localhost:8080/v1
  api_key: anything
```

`quality` selects freellmpool's capability-aware routing alias. Hermes still
sees a custom endpoint rather than a native provider, and freellmpool does not
edit `~/.hermes/config.yaml`. Upstream reference:
<https://github.com/NousResearch/hermes-agent/blob/a7d7c02cb6db071eced4ac82e24f878588619600/website/docs/integrations/providers.md#custom--self-hosted-llm-providers>.

### aider
```bash
export OPENAI_API_BASE=http://localhost:8080/v1
export OPENAI_API_KEY=anything
aider --model openai/auto
```

### Continue (VS Code / JetBrains)
`~/.continue/config.yaml`:
```yaml
models:
  - name: freellmpool
    provider: openai
    model: auto
    apiBase: http://localhost:8080/v1
    apiKey: anything
```

### Cline / Roo Code
Settings → **API Provider: OpenAI Compatible** → Base URL `http://localhost:8080/v1`,
API key `anything`, Model `auto`.

### Cursor / Windsurf
Settings → Models → enable **Override OpenAI Base URL** → `http://localhost:8080/v1`,
API key `anything`. (Free-tier models are slower than paid frontier models.)

### Claude Code
Claude Code can use the experimental Anthropic bridge at `/v1/messages`:

```bash
export ANTHROPIC_BASE_URL=http://localhost:8080
export ANTHROPIC_AUTH_TOKEN=dummy
export ANTHROPIC_API_KEY=dummy
export ANTHROPIC_MODEL=auto
export ANTHROPIC_SMALL_FAST_MODEL=auto
export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
claude
```

Pin a concrete backend with `ANTHROPIC_MODEL=provider/model`, for example
`alibaba_cloud_model_studio/qwen3-plus`.

### OpenAI Codex CLI
Codex speaks the Responses API, which freellmpool shims at `/v1/responses` — see
[AGENTS.md](AGENTS.md#openai-codex-cli).

## Chat UIs

### Open WebUI
Admin Panel → Settings → **Connections** → add an OpenAI API connection with URL
`http://localhost:8080/v1` and key `anything`.

### LibreChat
`librechat.yaml`:
```yaml
endpoints:
  custom:
    - name: "freellmpool"
      apiKey: "anything"
      baseURL: "http://localhost:8080/v1"
      models:
        default: ["auto"]
        fetch: true
```

### Lobe Chat
Settings → Language Model → OpenAI → set the **API Proxy Address** to
`http://localhost:8080/v1`, key `anything`.

## Frameworks & SDKs

### LangChain
```python
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(base_url="http://localhost:8080/v1", api_key="anything", model="auto")
```

### LlamaIndex
```python
from llama_index.llms.openai_like import OpenAILike
llm = OpenAILike(api_base="http://localhost:8080/v1", api_key="anything",
                 model="auto", is_chat_model=True)
```

### Vercel AI SDK
```ts
import { createOpenAI } from "@ai-sdk/openai";
const fp = createOpenAI({ baseURL: "http://localhost:8080/v1", apiKey: "anything" });
const { text } = await generateText({ model: fp("auto"), prompt: "..." });
```

### OpenAI SDK (Python / JS)
Set `OPENAI_BASE_URL=http://localhost:8080/v1` — see
[`examples/agent_openai_sdk.py`](../examples/agent_openai_sdk.py).

## CLI tools

### Simon Willison's `llm`
`~/.config/io.datasette.llm/extra-openai-models.yaml`:
```yaml
- model_id: freellmpool
  model_name: auto
  api_base: http://localhost:8080/v1
  api_key_name: freellmpool
```
Then: `llm -m freellmpool "Explain async/await"`.

### shell-gpt (`sgpt`)
`~/.config/shell_gpt/.sgptrc`:
```
API_BASE_URL=http://localhost:8080/v1
DEFAULT_MODEL=auto
OPENAI_API_KEY=anything
```

## Tailnet gateway

Serve the proxy over your Tailscale Tailnet so a remote agent or device can use
it without exposing it to the public internet:

```bash
# On the serving machine
freellmpool tailnet serve --port 8080
```

`tailnet serve` requires an API key by default. Omitting `--api-key` generates a
session token and prints it exactly once. Do not run unauthenticated over a
non-loopback interface.

From a remote machine, copy the serving IP and token from `freellmpool tailnet
connect`:

```bash
freellmpool tailnet connect <tailnet-ip> --port 8080
```

Both commands degrade to loopback guidance when Tailscale is missing or logged out.
When auth is enabled, `/playground` and all model API routes require the same
proxy key as `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`.

## Roles, recipes, jobs, and reports

`freellmpool` also exposes helper workflows that do not need a third-party tool:

- `freellmpool roles` lists ask-role presets such as `coder`, `critic`,
  `summarizer`, `cheap`, `fast`, and `second-opinion`.
- `freellmpool ask --second-opinion` runs a bounded multi-model panel and can
  synthesize the answers.
- `freellmpool battle "..."` prints a side-by-side Markdown comparison.
- `freellmpool playground` opens the local `/playground` page served by the proxy.
- `freellmpool recipe run pr-review --input patch.diff` and
  `freellmpool recipe run repo-summary --path 'src/freellmpool/*.py'` run
  versioned JSON workflows.
- `freellmpool jobs add --recipe ...` queues slow, quota-aware work to a local
  foreground JSONL queue. `jobs run` is foreground-only; `jobs watch` is a
  one-shot replay render, not a daemon.
- Completed recipe jobs create run records and Markdown reports. Inspect them
  with `freellmpool report list`, `freellmpool report last --markdown`,
  `freellmpool report last --html --path`, and `freellmpool cost show <run-id>`.
  Completed ask jobs keep output on the job event itself.

Run `freellmpool recipe list` and `freellmpool jobs --help` for the full surface.

## Provider caveats

All provider traffic leaves your machine. Keyless endpoints are public, and
keyed endpoints use your own credentials. Free-tier model IDs, daily caps, and
availability drift without notice. freellmpool does not bypass provider rate
limits, rotate accounts, or evade quotas.

## Automation

### n8n
In the **OpenAI** node's credential, set the **Base URL** to
`http://localhost:8080/v1` and any API key.

---

> Got a tool working that isn't listed? A PR adding it here is very welcome —
> see [CONTRIBUTING.md](../CONTRIBUTING.md). Config details for third-party tools
> change over time; check the tool's own docs if a field has moved.
