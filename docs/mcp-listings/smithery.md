# Smithery Submission Copy

Use this after preparing a local MCPB bundle for the stdio server. Smithery's URL
path is for Streamable HTTP servers; freellmpool currently ships a local stdio
server through PyPI.

## Name

freellmpool

## Package / command

```json
{
  "command": "uvx",
  "args": ["freellmpool", "mcp"]
}
```

## Short description

Run free LLM subtasks through MCP: ask, panel, second-opinion, battle, recipe,
roles, tailnet info, quota-wise advice, tokenmax, routing preview, available
models, daily quota, and lifetime stats.

## Long description

freellmpool is a local stdio MCP server for using accessible LLM provider routes
from Claude Desktop, Claude Code, Cursor, and other MCP clients. It can work
without provider credentials while an enabled keyless route is available; users
can add applicable provider credentials for more routes and capacity. The server exposes direct MCP tools for
single-model asks, multi-model panels, agent-facing second-opinion flows,
Markdown battle comparisons, bundled recipes, role presets, safe Tailscale
Tailnet setup hints, local quota-wise headroom advice, maximum fan-out
`tokenmax`, routing explanations, model discovery, quota status, and lifetime
stats.

## Links

- Repository: https://github.com/0xzr/freellmpool
- Docs: https://0xzr.github.io/freellmpool/
- MCP docs: https://github.com/0xzr/freellmpool/blob/main/docs/MCP.md
- Package: https://pypi.org/project/freellmpool/
- Manifest: https://github.com/0xzr/freellmpool/blob/main/server.json

## Notes

- Transport: stdio.
- No provider secret is required while an enabled keyless route is available.
- Optional provider keys are read from the local environment only.
- Prompts are routed to whichever free provider/model serves the selected tool
  call; see FAQ for provider/privacy notes.
