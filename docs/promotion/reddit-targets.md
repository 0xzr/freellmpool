# Reddit posting targets

Last researched: 2026-06-18. Re-check subreddit rules before posting; Reddit
rules, sticky threads, and mod preferences change.

## Ground rules

- Disclose affiliation plainly: "I maintain freellmpool."
- Do not ask for upvotes, stars, comments, or brigading. It is fine to say a
  star helps discovery after you have already provided a useful technical post.
- Do not spray the same copy across communities. Each subreddit needs its own
  workflow, caveats, and ask.
- Use PNG assets on Reddit: `assets/demo.png`, `assets/tokenmax-results.png`,
  or `assets/social-preview.png`.
- Put the GitHub link in a comment when the subreddit asks for links in
  comments.
- Do not imply privacy. Prompts go to the selected upstream provider.
- Do not imply rate-limit bypass. freellmpool uses legitimate provider free
  tiers and fails over when routes are unavailable or rate-limited.
- Avoid "game changer", "revolutionary", "unlimited", and other launch-bro copy.
  Communities are actively hostile to AI-generated promotion slop.
- Cap the first wave at one Reddit post per day and one post per subreddit.
  Spend the rest of the time answering comments and posting only in threads
  where someone is already asking for the workflow.

## Image captions and alt text

Use these when uploading PNGs:

| Asset | Caption | Alt text |
| --- | --- | --- |
| `assets/demo.png` | Terminal demo showing freellmpool routing through its local proxy and reporting catalog/provider status. | Screenshot of a terminal running freellmpool with proxy, provider catalog, and routing output. |
| `assets/tokenmax-results.png` | tokenmax summary card showing enabled routes, cataloged providers, keyless start, and model fan-out behavior. | Social card for freellmpool tokenmax with stats for enabled routes, cataloged providers, and keyless start. |
| `assets/social-preview.png` | Project preview card for freellmpool: free LLM API pool for agents and local proxies. | Dark social preview image for freellmpool with feature labels for keyless start, 24 providers, OpenAI proxy, MCP, transcription, and tokenmax. |

## Already posted

| Subreddit | Fit | What to do |
| --- | --- | --- |
| `r/LLMDevs` | Strong, but already used | Do not repost. A freellmpool post already exists: <https://www.reddit.com/r/LLMDevs/comments/1tw8jv6/i_pooled_16_free_llm_api_tiers_behind_one_openai/>. Reply only when people ask about free APIs, provider drift, Cline/OpenCode/Cursor setup, or new routes. If needed, mention that the catalog has grown since the original "16 tiers" copy. |

## Tier 1 standalone targets

| Subreddit | Use this file | Why it fits | Required angle |
| --- | --- | --- | --- |
| `r/opencode` | `reddit-opencode.md` | Active OpenCode community with posts about free models, model limits, configs, and OpenAI-compatible endpoints. | Make it an OpenCode provider/config post, not a generic freellmpool launch. Be explicit that this is about the OpenCode app/CLI, not OpenCode Zen/Go. |
| `r/opencodeCLI` | `reddit-opencode.md` | Very strong fit. Recent posts ask about free models, free-tier order, limits, and workflow. | Lead with the config snippet and the TUI/status plugin. Ask for route/config feedback. |
| `r/mcp` | `reddit-mcp.md` | Strong fit if the post is a concrete MCP server showcase. Rules allow self-promotion with disclosure, require launched services, and warn against AI-generated slop. | Use the `showcase` tag if available. Include exact `freellmpool mcp` setup and tools. |
| `r/modelcontextprotocol` | `reddit-mcp.md` | Good technical audience for MCP server design, trust, and tool naming feedback. | Ask for feedback on tool surface and setup flow. Keep the copy technical. |
| `r/LocalLLaMA` | `reddit-local-llama.md` | Large audience that uses local models plus hosted free tiers, but self-promotion is sensitive. | Frame as a hosted-free-tier complement, not local inference. Disclose affiliation and respect the 1/10 guideline. |
| `r/LocalLLM` | `reddit-local-llama.md` | Similar local-model audience with a smaller community and explicit self-promotion limits. | Use only if the hosted-provider caveat is prominent. Treat it as a second local-model community after `r/LocalLLaMA`. |
| `r/opensource` | `reddit-open-source.md` | Good FOSS audience if the ask is contribution-focused. | Emphasize MIT license, catalog drift, good-first issues, and why it is not a link farm post. |

## Tier 1 controlled-thread targets

| Subreddit | Use this file | Why it fits | Required angle |
| --- | --- | --- | --- |
| `r/AI_Agents` | `reddit-metaswarm.md` | Strong fit for agent review, second opinions, MCP, and multi-model workflows. | Post in the weekly project display thread or put links in comments, per rules. Use the metaswarm review-adapter workflow. |
| `r/ChatGPTCoding` | `reply-bank.md` plus `reddit-open-source.md` | Has recurring self-promotion threads and many free API/coding-agent questions. | Self-promotion thread only, once per project. Also reply when someone asks for free coding APIs. |
| `r/github` | `reddit-open-source.md` | GitHub project promotion belongs in the pinned self-promotion megathread. | Megathread only. Include repo, what it does, target users, and contribution asks. |
| `r/Python` | `reddit-open-source.md` | freellmpool is a Python package and CLI. | Use Showcase format: "What My Project Does", "Target Audience", "Comparison". Do not use generic launch copy. |
| `r/MachineLearning` | `reddit-open-source.md` | Accepts self-promotion threads. Lower fit unless framed as developer tooling for LLM experiments. | Self-promotion thread only. Mention it is not a paper or model release. |

## Workflow-specific or reply-only targets

Use these when an exact thread asks for the workflow. Do not post standalone
launches unless the community rules and recent posts clearly support it.

| Subreddit | Post type | Useful angle |
| --- | --- | --- |
| `r/ClaudeCode` | Standalone only if Claude Code-specific; otherwise reply | "Use the MCP server for free second-opinion panels and tokenmax without spending Claude Code quota." Include `claude mcp add freellmpool -- freellmpool mcp`. |
| `r/ClaudeAI` | Reply or workflow post only | Same as Claude Code, but only if the thread is specifically about Claude Code/MCP/workflows. Generic AI/tool promotion will read off-topic. |
| `r/codex` | Reply or allowed self-promo window only | "Run a local OpenAI-compatible proxy for side tasks and model comparison while Codex stays on stronger paid models." Verify any self-promo day/thread before posting. |
| `r/CursorAI` and `r/cursor` | Reply or workflow post only | Cursor MCP plus custom OpenAI base URL. Use when people ask about MCP tools, alternate models, or free-tier coding help. |
| `r/CLine` | Reply-heavy | Strong fit for "free API alternative" and "cheap coding agent" threads. Point to OpenAI-compatible setup and caveats. |
| `r/Rag` | Reply-heavy | Use only on "free LLM API" or prototype-cost threads. Mention this is hosted upstream inference, not private local RAG. |
| `r/learnmachinelearning` | Reply-heavy | Useful for beginners asking for free LLM APIs. Keep it educational and caveated. |
| `r/datascience` | Reply only | Use for portfolio/demo free-tier questions, not a launch. |
| `r/GithubCopilot` | Reply only | Only when people discuss OpenAI-compatible endpoints, MCP, or coding-agent quotas. |
| `r/OpenAIDev` | Reply or technical post only | Fit is the OpenAI-compatible proxy and `/v1` surface, not "free LLMs" broadly. |
| `r/LangChain` and `r/LlamaIndex` | Reply or integration snippet only | Use when someone asks about cheap/free model backends or OpenAI-compatible base URLs. |
| `r/LocalAIServers` | Reply only | Be careful: freellmpool is local software over hosted providers, not a local model server. |
| `r/LocalAI` | Verify before using | Search did not surface a clearly active public subreddit; if it exists and is public, treat it like `r/LocalLLM` with strong hosted-provider caveats. |
| `r/vibecoding` | Only after mod approval | Rules require vibe-coding dev tools to be approved before a "shill" post. If approved, post a real workflow, not a launch. |

## Low-fit or skip

| Subreddit | Recommendation | Reason |
| --- | --- | --- |
| `r/metaswarm` | No action | No dedicated subreddit found as of 2026-06-18. Use `r/AI_Agents`, `r/ClaudeCode`, `r/codex`, or the metaswarm project/community instead. |
| `r/programming` | Skip for now | High scrutiny around LLM content and self-promotion. Use only for a genuinely technical article if rules allow. |
| `r/selfhosted` | Usually skip | freellmpool runs locally but routes prompts to hosted upstream providers, so it is not a true self-hosted inference solution. |
| `r/OpenAI`, `r/ChatGPT`, broad AI subs | Usually skip | Too broad and promotion-hostile unless a thread asks exactly for OpenAI-compatible free-tier tooling. |
| `r/MCPservers` | Cautious | On-topic, but self-promotion/listing rules are more directory-like and flair-dependent. Prefer `r/mcp` or `r/modelcontextprotocol` first; use `r/MCPservers` only with the right flair or mod guidance. |
| `r/ArtificialInteligence` | Thread only | Broad, lower-fit community with recurring self-promotion threads. |

## Research notes

- `r/mcp` rules: no waitlists, no AI-generated slop, no astroturfing, use
  showcase tag for your own MCP work: <https://www.reddit.com/r/mcp/about/>
- `r/AI_Agents` rules: links in comments, project promotion in weekly project
  display thread, and 1/10 self-promotion ratio:
  <https://www.reddit.com/r/AI_Agents/>
- `r/LocalLLaMA` rules: disclose affiliation and keep self-promotion under the
  1/10 guideline: <https://www.reddit.com/r/LocalLLaMA/>
- `r/github` rule: promote projects only in the pinned megathread:
  <https://www.reddit.com/r/github/about/>
- `r/Python` showcase rule: include "What My Project Does", "Target Audience",
  and "Comparison": <https://www.reddit.com/r/Python/>
- `r/vibecoding` rule: dev tools must be approved first:
  <https://www.reddit.com/r/vibecoding/>
- `r/LocalLLM` rules: topic relevance, low-effort filtering, and 1/10
  self-promotion guidance: <https://www.reddit.com/r/LocalLLM/>
- `r/LocalAIServers` is public and focused on locally hosted AI servers; use
  replies only because freellmpool routes hosted upstream providers:
  <https://www.reddit.com/r/LocalAIServers/>
