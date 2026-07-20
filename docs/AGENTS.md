# Using freellmpool as the free LLM backend for AI agents

Most agent frameworks and coding agents speak the **OpenAI API**. Because
`freellmpool proxy` *is* an OpenAI-compatible endpoint, you can point them at it
and they'll run on pooled free-tier inference — with failover when one provider
rate-limits you mid-run (exactly when long agent loops tend to die).

## Release status

- **Latest release: 0.11.4.** GitHub and PyPI both provide 0.11.4, including
  `freellmpool/spread` routing.
- **Current main includes unreleased changes**: the Hermes profile, public
  `/livez` and `/readyz`, authenticated `/v1/providers`,
  `/v1/models?ready=true`, and registry-readiness hardening for the existing
  repository-local OpenCode plugins. Replace the released package in your agent
  environment to test that source:

  ```bash
  python -m pip install --force-reinstall 'git+https://github.com/0xzr/freellmpool.git@main'
  ```

- **Registry publication status: pending.** `opencode-freellmpool` and
  `opencode-freellmpool-tui` were not published on npm as of 2026-07-19; use
  their local-file install paths.

Start the gateway once:

```bash
freellmpool proxy --port 8080
export OPENAI_BASE_URL=http://localhost:8080/v1
export OPENAI_API_KEY=anything   # ignored by freellmpool
```

Then wire up your tool of choice.

On current `main`, orchestrators can use `/livez` for liveness, `/readyz` for an
advisory local quota/cooldown snapshot, authenticated `/v1/providers` for
secret-free provider readiness, and `/v1/models?ready=true` for ready targets.
These endpoints do not probe upstream providers.

For structured setup, use profiles:

```bash
freellmpool profile list
freellmpool profile show opencode
freellmpool profile install opencode
freellmpool profile doctor opencode --dry-run
```

`freellmpool code <agent>` remains a compatibility shortcut that renders the
same profile quick-start. `profile install <agent>` is print-only; it does not
edit third-party config files.

## OpenAI Python SDK / OpenAI Agents SDK

```python
from openai import OpenAI

client = OpenAI()  # reads OPENAI_BASE_URL + OPENAI_API_KEY
resp = client.chat.completions.create(
    model="auto",  # let freellmpool pick the least-used free provider
    messages=[{"role": "user", "content": "Plan a 3-step refactor of foo.py"}],
)
print(resp.choices[0].message.content)
```

See [`examples/agent_openai_sdk.py`](../examples/agent_openai_sdk.py) for a
runnable version.

## Claude Code

Claude Code speaks the **Anthropic Messages API**, which freellmpool shims at
`/v1/messages` — so it can run on free models:

```bash
freellmpool proxy --port 8080
export ANTHROPIC_BASE_URL=http://localhost:8080
export ANTHROPIC_API_KEY=anything
claude   # now on free models
```

> Experimental. Text and tool-use (the agentic file-editing loop) are translated;
> vision isn't yet. Free models are weaker than Claude — great for cheap iteration,
> not a full replacement. `freellmpool code claude` prints this.

## OpenAI Codex CLI

Codex speaks the **Responses API**, which `freellmpool` shims at `/v1/responses`:

```bash
freellmpool proxy --port 8080
export OPENAI_BASE_URL=http://localhost:8080/v1
export OPENAI_API_KEY=anything
codex --config model_provider=openai   # or set base URL in ~/.codex/config.toml
```

> The Responses shim is minimal (text in/out, streaming events). It's great for
> running Codex/agents on free inference for everyday coding; tool-calling and
> richer Responses features are a work in progress.

## Hermes Agent (current main; unreleased)

Hermes supports an OpenAI-compatible custom endpoint. The first-class profile
prints the supported config and never edits `~/.hermes/config.yaml`:

```bash
freellmpool profile install hermes
freellmpool profile doctor hermes --dry-run
# Interactive equivalent: hermes model → Custom endpoint → http://localhost:8080/v1
```

Use model alias `quality` in Hermes. For long OpenCode or other OpenAI-compatible
agent loops, start with `freellmpool/spread` to rotate across the least-used
provider tier before using latency/health as a tie-break.

## Metaswarm external-tools review

Metaswarm can use `freellmpool` as a review-only external tool. The integration
is in [`integrations/metaswarm`](../integrations/metaswarm): copy
`freellmpool-review-adapter.sh` into `.metaswarm/adapters/freellmpool.sh`, then
add it to `.metaswarm/external-tools.yaml` with roles `review` and
`second_opinion`.

The adapter is deliberately not an implementer. It reviews a worktree diff
against a spec/rubric, runs a configurable strong-model panel through
`freellmpool`, and emits a metaswarm-style JSON envelope. If no strong provider
key is configured (`MISTRAL_API_KEY`, `NVIDIA_API_KEY`, or `OPENROUTER_API_KEY`
by default), it returns `error_type: "auth_missing"` before any provider call.

The first-class profile is:

```bash
freellmpool profile show metaswarm
freellmpool profile doctor metaswarm --dry-run
```

It documents one free/cheap worker lane through the local proxy, one larger
reviewer lane, Tailnet URL setup for remote agents, and Codex/Opus as explicit
user-owned paid escalation/final-review tools. freellmpool does not select those
paid lanes silently.

The copy-pastable setup path is:

```bash
freellmpool init --yes --agent metaswarm --tailnet
freellmpool profile install metaswarm
freellmpool tailnet serve --port 8080
freellmpool profile doctor metaswarm --dry-run
```

## Workflows that help agents

- `freellmpool roles` shows role presets (`coder`, `critic`, `summarizer`,
  `second-opinion`, ...).
- `freellmpool ask --role coder --second-opinion` can review an implementation
  plan before a long agent run.
- `freellmpool battle "which prompt version is clearer?"` compares model answers
  side by side.
- `freellmpool recipe run metaswarm-worker-review --input worker.md --validation-output-file validation.txt`
  returns a structured review of a worker summary.
- `freellmpool jobs add --recipe pr-review --input patch.diff` queues slow,
  quota-aware review work for foreground processing with `freellmpool jobs run`.
  Completed recipe jobs create run records you can inspect with
  `freellmpool report list`, `freellmpool report last --markdown`, and
  `freellmpool cost show <run-id>`.

## aider (AI pair programming in your terminal)

```bash
export OPENAI_API_BASE=http://localhost:8080/v1
export OPENAI_API_KEY=anything
aider --model openai/auto
```

## Continue / Cline / any "OpenAI-compatible" provider box

In the provider settings, set:

- **Base URL:** `http://localhost:8080/v1`
- **API key:** anything
- **Model:** `auto`, or pin one like `groq/llama-3.3-70b-versatile`

## LangChain

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:8080/v1",
    api_key="anything",
    model="auto",
)
print(llm.invoke("Summarize the singleton pattern in one line.").content)
```

## Why this is nice for agents specifically

- **Failover mid-run.** A long agent loop that would otherwise die on a single
  provider's `429` transparently rolls to the next free pool.
- **More total throughput.** Agents are token-hungry; pooling several free
  tiers multiplies your daily ceiling.
- **One config.** Point every tool at one base URL instead of juggling a
  different SDK and key per provider.

> Heads up: free-tier models are smaller/faster than frontier models. They're
> great for triage, drafting, classification, and tool-routing steps; reach for
> a frontier model for the hardest reasoning. `freellmpool` is about making the
> cheap-and-plentiful path effortless, not replacing GPT-class models.
