# GitHub Discovery Checklist

This records the current GitHub metadata status and the remaining UI-only
promotion actions.

## Metadata audit

Repository metadata reverified on 2026-08-29 with:

```bash
gh repo view 0xzr/freellmpool --json description,repositoryTopics,openGraphImageUrl,stargazerCount,url
```

Current About description (117 chars):

> Free LLM gateway: 22 LLM providers, 177 enabled chat routes, 431 cataloged chat models; keyless start when available.

Current 20-topic set:

`anthropic`, `claude`, `codex`, `cursor`, `failover`, `free-llm`,
`free-llm-api`, `gemini`, `groq`, `llm-gateway`, `llm-router`, `mcp`,
`mcp-server`, `model-context-protocol`, `openai`, `openai-proxy`,
`openrouter`, `python`, `rate-limiting`, `speech-to-text`.

The previous P9 gap topics are now present: `anthropic`, `claude`, `cursor`,
`speech-to-text`, and `rate-limiting`.

## Applied metadata

The topic and description refreshes have been applied. Description command used:

```bash
gh repo edit 0xzr/freellmpool \
  --description "Free LLM gateway: 22 LLM providers, 177 enabled chat routes, 431 cataloged chat models; keyless start when available."
```

## Social preview

Prepared asset:

- `assets/social-preview.png` at 1280x640 and under 1 MB, ready to upload.
- `assets/social-preview.svg` as the editable source.

GitHub's social preview docs recommend PNG, JPG, or GIF under 1 MB, ideally
1280x640:

https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview

Custom social preview upload is still manual UI-only. `gh repo view` returns a
GitHub-generated `openGraphImageUrl`, which is not proof that the custom asset has
been uploaded.

Operator steps before launch:

1. Open `0xzr/freellmpool` on GitHub.
2. Go to Settings, then Social preview.
3. Upload `assets/social-preview.png`.

## Profile pin

Pin `0xzr/freellmpool` on the owner profile through GitHub's profile
customization UI. The operator reported this done on 2026-06-17; recheck
manually before launch because it cannot be verified from repository files.
