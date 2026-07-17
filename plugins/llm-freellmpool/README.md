# llm-freellmpool

[![PyPI](https://img.shields.io/pypi/v/llm-freellmpool.svg)](https://pypi.org/project/llm-freellmpool/)

A plugin for [llm](https://llm.datasette.io) that lets you run **free LLMs** through
[freellmpool](https://github.com/0xzr/freellmpool) — which pools the free tiers of
22 providers (Groq, Cerebras, NVIDIA NIM, Gemini, OpenRouter, Cloudflare, …) with
automatic failover. **It works with zero API keys** thanks to freellmpool's keyless
providers.

## Install

```bash
llm install llm-freellmpool
```

## Use

```bash
llm -m freellmpool "Explain the CAP theorem in one sentence."
```

That's it — no key required. Pipe context in like any `llm` model:

```bash
cat error.log | llm -m freellmpool "What's the root cause?"
```

Pick a specific free provider or model with the `target` option:

```bash
llm -m freellmpool -o target groq "Say hi"
llm -m freellmpool -o target groq/llama-3.3-70b-versatile "Say hi"
```

## More providers

Add provider keys as environment variables (`GROQ_API_KEY`, `CEREBRAS_API_KEY`, …)
to unlock more models and higher limits. See the
[freellmpool docs](https://github.com/0xzr/freellmpool/blob/main/docs/ACCOUNTS.md).

## License

MIT
