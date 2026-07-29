# Security Merge Enforcement

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
