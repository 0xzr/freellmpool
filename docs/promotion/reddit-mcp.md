# Reddit / MCP draft

Targets: `r/mcp` first, `r/modelcontextprotocol` second. Use `r/MCPservers`
only with the right flair or mod guidance.

Use this as a technical MCP server showcase. Do not post it as a generic launch
or a sensational MCP security/productivity claim.

Transport clarification: freellmpool speaks MCP over local stdio to MCP clients.
It is a local server process started by Claude Code, Claude Desktop, Cursor, or
another host. It is not a hosted/remote MCP service.

## Title options

- I built a local MCP server for free-model second opinions and tokenmax fan-out
- freellmpool MCP: hand off small tasks to free LLM tiers from Claude Code/Cursor
- MCP server for routing self-contained subtasks across free LLM providers

## Body

I maintain `freellmpool`, an MIT-licensed Python tool that pools legitimate
hosted free LLM tiers. The MCP part is a local stdio server that lets Claude
Desktop, Claude Code, Cursor, and other MCP clients hand off self-contained
subtasks to free models.

Repo: https://github.com/0xzr/freellmpool

Install:

```bash
pip install freellmpool
```

Claude Code:

```bash
claude mcp add freellmpool -- freellmpool mcp
```

This assumes Claude Code is already installed and configured.

Claude Desktop / Cursor config:

```json
{
  "mcpServers": {
    "freellmpool": {
      "command": "freellmpool",
      "args": ["mcp"]
    }
  }
}
```

The MCP server exposes:

- `free_llm_ask`: ask one free model, with optional routing/model/provider;
- `free_llm_panel`: ask the same prompt to several models and compare answers;
- `tokenmax`: fan one prompt across every available free model, with progress
  notifications when the MCP host supports them;
- `free_llm_route`: preview where a prompt would route without spending a token;
- `free_llm_models`: list available `provider/model` ids;
- `free_llm_quota`: show today's provider usage and headroom;
- `free_llm_stats`: show lifetime tokens served free and estimated cost avoided.

Use cases that have worked well:

- ask a cheap/free model for a second opinion before spending Claude/Codex quota;
- summarize a doc or issue from inside the coding-agent session;
- classify or triage bug reports;
- compare multiple small models before deciding whether a task needs a stronger
  model;
- route quick "what changed here?" questions away from the main agent.

The current packaged catalog has 24 cataloged providers, 222 enabled chat
routes, and 407 cataloged chat models. It can start with default
keyless/key-optional routes when they are available, and provider keys can be
added for more capacity.

Caveats:

- It is not a privacy layer. Prompts go to the selected upstream provider.
- It respects provider rate limits and fails over when a route is unavailable.
- Free-provider catalogs drift, so route reports and PRs are useful.
- The server is local stdio MCP. It is not a remote hosted MCP service.

I would like feedback from MCP users on tool names, setup docs, and whether the
tool surface should expose more routing/quota details to the host.

## Posting checklist

- In `r/mcp`, use the showcase tag if available.
- Disclose affiliation in the first sentence.
- Keep the GitHub link visible but do not make the post link-only.
- Use `assets/tokenmax-results.png` if including an image.
  Caption: "tokenmax summary card showing enabled routes, cataloged providers,
  keyless start, and model fan-out behavior."
  Alt text: "Social card for freellmpool tokenmax with stats for enabled routes,
  cataloged providers, and keyless start."
