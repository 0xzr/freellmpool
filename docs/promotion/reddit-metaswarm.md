# Reddit / metaswarm and agent-workflow draft

Primary target: `r/AI_Agents` weekly project display thread.

Secondary targets: `r/ClaudeCode`, `r/codex`, `r/agenticAI`, or a metaswarm
project/community channel if one exists. No dedicated `r/metaswarm` subreddit
was found as of 2026-06-18.

Use this when the audience cares about agent workflows and review loops. It
should not read like "free models are amazing." The useful story is:
freellmpool can provide cheap independent review/second opinions while stronger
coding agents keep doing implementation.

## Title options

- Using freellmpool as a review-only metaswarm adapter for cheap second opinions
- Agent workflow: free-model panel review before merging coding-agent changes
- I wired metaswarm to freellmpool for review-only adversarial checks

## Body

I maintain `freellmpool`, an MIT-licensed local router over legitimate hosted
free LLM tiers. One concrete use case is a review-only adapter for metaswarm
external-tools: coding agents still implement changes, while freellmpool supplies
independent review and second-opinion panels.

Repo: https://github.com/0xzr/freellmpool

Why I set it up this way:

- free models are often good enough for "does this plan/doc/code smell wrong?";
- multiple weaker models can catch different issues than the writer model;
- review traffic is bursty and cheaper to offload than implementation traffic;
- fail-closed auth checks are better than silently running a weak or unavailable
  review path.

Install the adapter into a metaswarm project:

```bash
mkdir -p .metaswarm/adapters
cp integrations/metaswarm/freellmpool-review-adapter.sh .metaswarm/adapters/freellmpool.sh
chmod +x .metaswarm/adapters/freellmpool.sh
python -m pip install freellmpool
```

This assumes metaswarm is already installed and configured in the project.

Configure strong review providers, for example:

```bash
export MISTRAL_API_KEY=...
export NVIDIA_API_KEY=...
export OPENROUTER_API_KEY=...
```

`.metaswarm/external-tools.yaml`:

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
      - "nvidia/moonshotai/kimi-k3"
      - "nvidia/nvidia/nemotron-3-ultra-550b-a55b"
      - "mistral/mistral-medium-latest"
      - "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"
      - "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
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

The adapter intentionally rejects `implement`. It only supports review and
second-opinion roles because freellmpool routes prompts to model providers; it
does not own a worktree editing contract.

If required strong provider keys are missing, review fails closed with
`error_type: "auth_missing"` before any provider call. That stops the review
call; it does not silently substitute another provider. That is deliberate: a
review adapter should be obviously unavailable rather than silently downgrading
to an untrusted path.

Use cases:

- adversarial review after an agent has drafted a plan or PR;
- second opinions on promotional copy, docs, and release notes;
- multi-model disagreement checks before merging;
- cheap review of small agent-generated diffs;
- routing a hard review prompt to a panel while keeping paid frontier quota for
  implementation.

Caveats:

- Prompts go to selected upstream providers. Do not send secrets unless those
  providers are acceptable for the data.
- Free/provider-backed models drift and can disappear.
- This is a review adapter, not a replacement for human merge judgment.

For `r/AI_Agents`, put the repo link in the comments if required by the current
weekly thread rules.

## Image

Use `assets/tokenmax-results.png` if a thread allows images.

Caption: "tokenmax summary card showing enabled routes, cataloged providers,
keyless start, and model fan-out behavior."

Alt text: "Social card for freellmpool tokenmax with stats for enabled routes,
cataloged providers, and keyless start."
