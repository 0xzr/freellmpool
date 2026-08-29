# Glama Submission Copy

## GitHub repository

https://github.com/0xzr/freellmpool

## Name

freellmpool

## Category suggestions

- AI & Machine Learning
- Coding Agents
- Developer Tools

## Install command

```json
{
  "command": "uvx",
  "args": ["freellmpool", "mcp"]
}
```

## Description

freellmpool is a local stdio MCP server that lets Claude Desktop, Claude Code,
Cursor, and other MCP clients offload subtasks to pooled free LLM tiers. It can
start with no API keys when a keyless provider is available, supports optional
user-owned free-tier keys, and exposes tools for single asks, model panels, second-opinion
and battle comparisons, bundled recipes, role presets, Tailnet setup, quota-wise
advice, tokenmax fan-out, routing previews, model listing, quota status, and
lifetime stats.

## Tool names

- `free_llm_ask`
- `free_llm_panel`
- `free_llm_second_opinion`
- `free_llm_battle`
- `free_llm_recipe`
- `free_llm_roles`
- `free_llm_tailnet_info`
- `free_llm_quota_wise`
- `tokenmax`
- `free_llm_route`
- `free_llm_models`
- `free_llm_quota`
- `free_llm_stats`

## Maintainer notes

The server runs locally and speaks MCP over stdio. It does not store provider
secrets. Optional provider keys are read from environment variables or local
config. See `FAQ.md` for provider prompt-destination and ToS notes.
