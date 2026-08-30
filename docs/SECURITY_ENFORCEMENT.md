# Security enforcement

## Repository merge controls

The repository-level workflows are only gates when GitHub requires their
results. The live `main` protection must retain the existing CI contexts and
also require these GitHub Actions check contexts:

- `High-severity Python source audit`
- `Python dependency audit`
- `GitHub Actions audit`
- `Container vulnerability audit`
- `Analyze Python`

The repository also has active ruleset `19967943`, named `CodeQL high-severity
merge protection`. It targets the default branch, requires the `CodeQL` tool,
uses `high_or_higher` for security alerts, and uses `errors` for non-security
alerts. There are no bypass actors.

Administrators can verify the live controls without changing them:

```bash
gh api repos/0xzr/freellmpool/branches/main/protection/required_status_checks \
  --jq '{strict, contexts}'
gh api repos/0xzr/freellmpool/rulesets \
  --jq '.[] | select(.name == "CodeQL high-severity merge protection") | .id'
gh api repos/0xzr/freellmpool/rules/branches/main
```

After changing workflow job names, update branch protection in the same pull
request and verify the exact PR head reports every replacement context before
merging. Never remove an existing required context merely to unblock a merge.
The CodeQL ruleset should remain active; use GitHub's code-scanning dismissal
workflow with a linked issue for reviewed false positives.

## Runtime security boundaries

- `/dashboard` and `/playground` are public only as a self-contained, data-free
  browser shell. Usage, provider inventory, ready models, and battle requests
  stay behind bearer-header authentication when proxy auth is configured.
  After form submission the token input is cleared and the token remains only
  in a JavaScript closure. It is never copied into a URL, cookie, Web Storage,
  rendered DOM, global, log, or HTML response, and a reload requires re-entry.
- `freellmpool local discover` probes only the fixed LM Studio, Ollama, and
  llama.cpp literal-loopback endpoints, or one explicit canonical
  literal-loopback URL. Requests have strict time/body/model bounds, send no
  credentials, and follow no redirects. Discovery never performs DNS, LAN, or
  process scanning. Import requires `--yes`, creates pin-only routes, and is
  reversible with `local remove --yes`.
- Streaming failover stops at downstream commit. Text-only Responses and
  Anthropic Messages content is delivered incrementally; tool and rich-content
  translation remains buffered. A post-commit upstream failure is never
  retried elsewhere and never ends with a successful terminal event.
- `freellmpool doctor` reports strict config syntax/table errors with sanitized
  line, column, and type metadata. It does not echo malformed values, API keys,
  bearer tokens, or other configuration secrets.
