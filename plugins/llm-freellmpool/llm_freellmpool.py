"""llm plugin: free LLMs via freellmpool.

    llm install llm-freellmpool
    llm -m freellmpool "Explain the CAP theorem in one sentence."

Can start with zero API keys when a freellmpool keyless provider is available.
Add provider keys as env vars to unlock more — see https://github.com/0xzr/freellmpool.
"""

from __future__ import annotations

import llm

from freellmpool import Pool


@llm.hookimpl
def register_models(register):
    register(Freellmpool())


class Freellmpool(llm.Model):
    model_id = "freellmpool"
    can_stream = False

    class Options(llm.Options):
        target: str | None = None  # "auto" | "groq" | "groq/openai/gpt-oss-20b"

    def execute(self, prompt, stream, response, conversation):
        pool = Pool.from_default_config()

        # Rebuild prior conversation turns as OpenAI-style messages.
        messages: list[dict[str, str]] = []
        if prompt.system:
            messages.append({"role": "system", "content": prompt.system})
        if conversation:
            for prev in conversation.responses:
                if prev.prompt.prompt:
                    messages.append({"role": "user", "content": prev.prompt.prompt})
                text = prev.text()
                if text:
                    messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": prompt.prompt})

        target = getattr(prompt.options, "target", None) or "auto"
        provider, model = None, None
        if target and target != "auto":
            if "/" in target:
                p, _, m = target.partition("/")
                provider, model = [p], m
            else:
                provider = [target]

        reply = pool.chat(messages, model=model, providers=provider)
        response.response_json = {"provider": reply.provider_id, "model": reply.model}
        yield reply.text
