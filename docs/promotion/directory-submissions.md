# Directory submission checklist

Some directories still need browser/account actions. This file keeps the exact
copy and status in one place for promotion work.

## Already done

- GitHub About description updated.
- GitHub topics updated.
- Good-first issues filed and refreshed.
- Official MCP Registry published.
- MCP.so submission comment posted.

## Manual GitHub UI

- Social preview upload:
  - Use `assets/social-preview.png`.
  - Open <https://github.com/0xzr/freellmpool/settings>.
  - Social preview -> Edit -> Upload an image.
- Profile pin:
  - Already reported done by operator.

## Glama

Submit: <https://glama.ai/mcp/servers>

Repository:

```text
https://github.com/0xzr/freellmpool
```

Short description:

```text
Local stdio MCP server for pooling free LLM provider tiers. Includes one-shot asks, multi-model panels, tokenmax fan-out, routing previews, model listing, quota status, and lifetime stats.
```

Paste more detail from:

```text
docs/mcp-listings/glama-submission.md
```

## PulseMCP

Submit: <https://www.pulsemcp.com/>

Repository:

```text
https://github.com/0xzr/freellmpool
```

Server manifest:

```text
https://github.com/0xzr/freellmpool/blob/main/server.json
```

Paste more detail from:

```text
docs/mcp-listings/pulsemcp-submission.md
```

## Smithery

Smithery's URL flow is for Streamable HTTP servers. `freellmpool` currently
ships a local stdio MCP server, so Smithery needs an MCPB bundle or an HTTP
wrapper before the listing is clean.

Use:

```text
docs/mcp-listings/smithery.md
```

## Optional directories after launch

- AlternativeTo, if it accepts developer tools.
- Product Hunt, only if a polished landing screenshot and demo are ready.
- Awesome MCP server lists, using the official registry listing as proof.
- Python/package newsletters, after PyPI release notes are stable.

## Image assets for directories

Use PNGs unless a directory explicitly accepts SVG:

- General social card: `assets/social-preview.png`
- Terminal demo: `assets/demo.png`
- Model fan-out/stat card: `assets/tokenmax-results.png`

Captions and alt text:

- `assets/social-preview.png`: "Project preview card for freellmpool: free LLM
  API pool for agents and local proxies." Alt text: "Dark social preview image
  for freellmpool with feature labels for keyless start, 22 providers, OpenAI
  proxy, MCP, transcription, and tokenmax."
- `assets/demo.png`: "Terminal demo showing freellmpool routing through its
  local proxy and reporting catalog/provider status." Alt text: "Screenshot of a
  terminal running freellmpool with proxy, provider catalog, and routing output."
- `assets/tokenmax-results.png`: "tokenmax summary card showing enabled routes,
  cataloged providers, keyless start, and model fan-out behavior." Alt text:
  "Social card for freellmpool tokenmax with stats for enabled routes, cataloged
  providers, and keyless start."
