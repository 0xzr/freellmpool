# llm-freellmpool

[![PyPI](https://img.shields.io/pypi/v/llm-freellmpool.svg)](https://pypi.org/project/llm-freellmpool/)

A plugin for [llm](https://llm.datasette.io) that runs LLM requests through
[freellmpool](https://github.com/0xzr/freellmpool). freellmpool has 22 cataloged providers
spanning recurring free tiers, keyless routes, finite trials,
pin-only routes, and disabled candidates; it automatically fails over only
across enabled routes you can access. It can start with zero API keys while an
enabled keyless route is available.

## Install

```bash
llm install llm-freellmpool
```

## Use

```bash
llm -m freellmpool "Explain the CAP theorem in one sentence."
```

No key is required while an enabled keyless route is available. Pipe context in like
any `llm` model:

```bash
cat error.log | llm -m freellmpool "What's the root cause?"
```

Pick a specific free provider or model with the `target` option:

```bash
llm -m freellmpool -o target groq "Say hi"
llm -m freellmpool -o target groq/openai/gpt-oss-20b "Say hi"
```

## More providers

Add applicable provider credentials as environment variables (`GROQ_API_KEY`,
`GEMINI_API_KEY`, …) to unlock more routes and capacity. Eligibility and terms
are provider-specific. See the
[freellmpool docs](https://github.com/0xzr/freellmpool/blob/main/docs/ACCOUNTS.md).

## License

MIT
