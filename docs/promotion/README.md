# freellmpool promotion pack

Prepared after the GitHub metadata, good-first issues, provider audit, and MCP
registry polish. Use this pack when posting externally.

## Current launch facts

- Repository: <https://github.com/0xzr/freellmpool>
- Docs: <https://0xzr.github.io/freellmpool/>
- PyPI: <https://pypi.org/project/freellmpool/>
- Version: `0.11.4`
- Catalog: 24 cataloged providers, 247 enabled chat routes, 405 cataloged chat models
- First-run hook: installs with `pip install freellmpool` and can answer with no
  API keys through default keyless/key-optional routes.
- Interfaces: CLI, Python library, OpenAI-compatible proxy, experimental
  Anthropic-compatible proxy path, and MCP server.
- Audio: OpenAI-compatible speech-to-text through the transcription endpoint.
- Strongest visual assets:
  - `assets/social-preview.png`
  - `assets/demo.png`
  - `assets/tokenmax-results.png`
  - SVG sources remain useful for README/docs, but use PNGs for Reddit,
    Product Hunt, GitHub social preview, and sites that do not render SVG
    previews reliably.

## Use this order

1. Finish manual GitHub UI work:
   - Upload `assets/social-preview.png` under repo Settings -> Social preview.
   - Keep the repo pinned on the `0xzr` profile.
2. Submit remaining MCP directories:
   - Glama
   - PulseMCP
   - Smithery, if you want to build an MCPB bundle for the stdio server.
3. Work Reddit in this order:
   - Start from `reddit-targets.md`; re-check each subreddit's rules on the day
     you post.
   - Do not repost to `r/LLMDevs`; a freellmpool launch post is already there.
     Use replies only.
   - Post OpenCode-specific copy from `reddit-opencode.md` to `r/opencode` or
     `r/opencodeCLI`.
   - Post MCP-specific copy from `reddit-mcp.md` to `r/mcp` or
     `r/modelcontextprotocol`.
   - Post the hosted-free-tier/local-complement angle from
     `reddit-local-llama.md` to `r/LocalLLaMA`.
   - Post the review/second-opinion workflow from `reddit-metaswarm.md` in the
     `r/AI_Agents` weekly project thread. There does not appear to be a
     dedicated `r/metaswarm` subreddit as of 2026-06-18.
   - Use megathreads or replies for lower-fit communities.
4. Post the broader launch:
   - Hacker News: `show-hn.md`
   - X, Bluesky, LinkedIn: `social-short-posts.md`
   - Product Hunt, if you can work the comments for a day: `product-hunt.md`
5. Use `reply-bank.md` for comments and hard questions.
6. Reuse `long-form-article.md` for a blog, Dev.to, Hashnode, or a GitHub
   Discussion.
7. Use `outreach-notes.md` for low-volume creator/newsletter outreach.

## Positioning

Keep the message narrow:

> A local, open-source pool for legitimate free LLM tiers. One CLI/proxy/MCP
> interface, automatic failover, no API keys needed to start, add your own free
> keys for more capacity.

Avoid overclaiming:

- Do not imply it is a replacement for frontier paid models.
- Do not imply all providers are private or equivalent.
- Do not imply it evades provider limits.
- Do not say "free forever" or imply provider free tiers are permanent.
- Do not say "unlimited".

## Primary CTA

Ask for stars and provider reports only after the useful part:

> If this saves you a Claude/Codex call, a GitHub star helps other developers
> find it. Provider free tiers drift, so model-id and limit reports are
> especially valuable.
