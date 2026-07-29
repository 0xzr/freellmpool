# Reddit / OpenCode draft

Targets: `r/opencode` first, `r/opencodeCLI` second.

Use this when posting to OpenCode communities. This should read like a working
OpenCode config/workflow note, not a generic freellmpool launch.

Important distinction: this is about using the OpenCode app/CLI with
freellmpool as a local OpenAI-compatible provider. It is not related to
OpenCode's commercial offerings. OpenCode Zen/Go is a separate service.
freellmpool catalogs OpenCode Zen routes, but they ship disabled by default
pending explicit opt-in and provider-policy review.

## Title options

- Using freellmpool as an OpenCode provider for free-tier model failover
- OpenCode config: one local proxy over free LLM tiers, with status tools
- I wired OpenCode to a local pool of free LLM tiers and would like config feedback

## Body

I maintain `freellmpool`, an MIT-licensed local router for legitimate hosted
free LLM tiers. I have been using it as an OpenCode provider so OpenCode can
talk to one local OpenAI-compatible endpoint instead of juggling provider keys,
model ids, and free-tier failures one by one.

This is not about OpenCode Zen/Go. It is an OpenCode app/CLI config that points
OpenCode at a local freellmpool proxy.

Repo: https://github.com/0xzr/freellmpool

Install and start the proxy:

```bash
pip install freellmpool
freellmpool proxy --port 8080
```

`opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "freellmpool/auto",
  "provider": {
    "freellmpool": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://localhost:8080/v1",
        "apiKey": "{env:FREELLMPOOL_PROXY_KEY}"
      },
      "models": { "auto": {}, "fast": {}, "quality": {}, "fair": {} }
    }
  }
}
```

Then choose:

- `freellmpool/auto` for the default route picker;
- `freellmpool/fast` for low-latency routes;
- `freellmpool/quality` for stronger free-tier routes when available;
- `freellmpool/fair` to spread quota.

The current packaged catalog has 24 cataloged providers, 222 enabled chat
routes, and 407 cataloged chat models. It can start keyless when default
keyless/key-optional routes are available, and you can add your own provider
free-tier keys for more capacity.

Why this is useful in OpenCode:

- try free models without editing OpenCode config for every provider;
- preserve paid/frontier quota for tasks that actually need it;
- use cheap/free models for docs, summaries, test triage, and small edits;
- fail over when a free provider returns 429, times out, disappears, or returns
  an empty reply;
- compare model behavior without turning OpenCode into a provider-config chore.

There are also two optional OpenCode plugins in the repo:

- `integrations/opencode-tui`: an embedded dashboard showing routing mode,
  tokens served free, estimated savings, provider race, latency sparkline, and
  last-served model.
- `integrations/opencode`: server plugin tools for `freellmpool_status` and
  `freellmpool_models`, plus a served-model toast.

Caveats:

- It is not a privacy layer. Prompts go to the selected upstream provider.
- It respects provider limits; it does not bypass them.
- Free-tier model quality and limits drift.
- Some cataloged routes are disabled by default until they are reliable or
  explicitly opted into.

I would like feedback from OpenCode users on the provider config, routing modes,
and whether the dashboard/status tools show the right information while you are
using free routes.

## Comment with asset

Use one PNG image, not SVG:

- `assets/demo.png` for the CLI/proxy terminal view.
  Caption: "Terminal demo showing freellmpool routing through its local proxy
  and reporting catalog/provider status."
  Alt text: "Screenshot of a terminal running freellmpool with proxy, provider
  catalog, and routing output."
- `assets/tokenmax-results.png` if the thread is about comparing models.
  Caption: "tokenmax summary card showing enabled routes, cataloged providers,
  keyless start, and model fan-out behavior."
  Alt text: "Social card for freellmpool tokenmax with stats for enabled routes,
  cataloged providers, and keyless start."
- `assets/social-preview.png` for a general repo preview.
  Caption: "Project preview card for freellmpool: free LLM API pool for agents
  and local proxies."
  Alt text: "Dark social preview image for freellmpool with feature labels for
  keyless start, 24 providers, OpenAI proxy, MCP, transcription, and tokenmax."
