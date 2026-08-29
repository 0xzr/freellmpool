# MCP Registry Listing Status

Updated on 2026-08-29 for the local `freellmpool 0.12.2` manifest. The official
MCP Registry has an active published entry and the MCP.so submission is complete;
the live active/latest endpoint below is authoritative for the version whose
publication has finished. Smithery, Glama, and PulseMCP still need account/web
UI actions.

## Local Manifest Status

`server.json` is ready for package-based stdio registry entries:

- Schema: `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`
- Name: `io.github.0xzr/freellmpool`
- Version: `0.12.2`
- Repository: `https://github.com/0xzr/freellmpool`
- Package: PyPI `freellmpool`, runtime hint `uvx`
- Transport: stdio
- Package argument: `mcp`

Install blocks for directories that accept raw client config:

```json
{
  "mcpServers": {
    "freellmpool": {
      "command": "uvx",
      "args": ["freellmpool", "mcp"]
    }
  }
}
```

After an explicit `pipx install freellmpool` or `pip install freellmpool`, users
can also configure:

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

Tool surface to mention in every listing:

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

## Registry Status

| Registry | Status | Requirement checked | Operator action |
|---|---|---|---|
| Official MCP Registry | Published; live endpoint is authoritative | `server.json` validates with `mcp-publisher`; PyPI package README includes hidden `mcp-name: io.github.0xzr/freellmpool` ownership metadata. | Active/latest listing: <https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.0xzr/freellmpool>. For each MCP package release, dispatch `.github/workflows/publish-mcp.yml` with the exact released version and immutable release source SHA, then require its normalized registry-equality verifier to pass. |
| Smithery | Ready for operator packaging; not directly URL-ready | Smithery URL publishing requires Streamable HTTP; local stdio publishing uses an MCPB bundle. Source: <https://smithery.ai/docs/build/publish>. | Build a local MCPB bundle for `freellmpool mcp`, then go to <https://smithery.ai/new>, choose **Local (MCPB Bundle)**, upload the bundle, and paste the copy from `docs/mcp-listings/smithery.md`. |
| Glama | Ready after merge | Glama lists open-source MCP servers from GitHub repo submission, verifies maintainer GitHub OAuth access, clones/builds/introspects the repo, and scores tools. Sources: <https://glama.ai/> and <https://glama.ai/mcp/methodology>. | Sign in, open <https://glama.ai/mcp/servers>, click **Submit** / **List your server for free**, enter `https://github.com/0xzr/freellmpool`, authorize with a GitHub account that has write/admin access, and paste the copy from `docs/mcp-listings/glama-submission.md` if the form asks for details. |
| MCP.so | Submitted | MCP.so says submissions are created via a GitHub issue and should include name, description, features, and connection information. Source: <https://mcp.so/>. | Submission comment: <https://github.com/chatmcp/mcpso/issues/1#issuecomment-4725456286>. Watch for maintainer import. |
| PulseMCP | Ready; may crawl from official registry | PulseMCP says its directory uses manual submissions, automated crawling, the official MCP Registry, and server.json metadata enrichment. Sources: <https://www.pulsemcp.com/api> and example listing <https://www.pulsemcp.com/servers/wei-mcp-registry>. | Wait for the official registry crawl, or open <https://www.pulsemcp.com/>, click **Submit server/client**, submit `https://github.com/0xzr/freellmpool`, and use `docs/mcp-listings/pulsemcp-submission.md`. |

## Submission Files

- `docs/mcp-listings/smithery.md`
- `docs/mcp-listings/glama-submission.md`
- `docs/mcp-listings/mcp-so-issue.md`
- `docs/mcp-listings/pulsemcp-submission.md`

External submissions performed: at least one official MCP Registry publish and
the MCP.so issue comment. The remaining directories require the operator's
account or browser UI.
