# freellmpool for Claude Code

Prepared by Sara Salem. Copyright (c) 2026 Sara Salem. MIT licensed.

A Claude Code plugin that connects the freellmpool MCP server, so Claude can call free and
keyless LLM providers for quick answers, second opinions, panels and battles.

## Contents

- `.mcp.json` – starts `uvx freellmpool mcp` (requires [uv](https://docs.astral.sh/uv/)).
- `skills/freellmpool` – tells Claude when and how to use the `free_llm_*` tools safely.
- `commands/` – `/free-ask`, `/second-opinion`, `/free-panel`, `/free-quota`.
- `agents/free-reviewer` – a review subagent that verifies free-model feedback.

## Install

```bash
claude plugin marketplace add SaraSalemAI/freellmpool
```

```bash
claude plugin install freellmpool@freellmpool
```

Or test locally from a checkout:

```bash
claude --plugin-dir integrations/claude-code
```

Add provider keys (optional) as described in the main README to unlock more models.
Free models may log prompts: never send secrets through them.
