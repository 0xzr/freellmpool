"""Core data types: Provider, Model, Reply."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Model:
    """A single model offered by a provider."""

    name: str
    rpd: int = 0  # free-tier requests-per-day hint; 0 = unknown/unmetered
    enabled: bool = True  # off-by-default models (failed live validation) are skipped by auto
    context: int | None = None  # optional context-window size (tokens); None = unknown
    auto: bool = True  # aliases stay pinnable but can be excluded from automatic fan-out/failover

    @property
    def key(self) -> str:
        """Unused-namespace-safe identifier, filled in by Provider."""
        return self.name


@dataclass(frozen=True)
class Provider:
    """A free-tier LLM endpoint and the models it serves."""

    id: str
    label: str
    adapter: str  # "openai" | "gemini" | "cloudflare"
    base_url: str
    models: tuple[Model, ...]
    key_env: str | None = None
    auth: str = "bearer"  # "bearer" | "none" (keyless)
    key_optional: bool = False  # works without a key, uses one if present
    extra_env: tuple[str, ...] = field(default_factory=tuple)

    @property
    def keyless(self) -> bool:
        """True if this provider can be used without any API key at all."""
        return self.auth == "none" or self.key_optional or not self.key_env

    def is_configured(self, env: dict[str, str] | None = None) -> bool:
        """True if this provider is usable: any extra env vars are present, and
        either it's keyless or its API key is set."""
        env = env if env is not None else dict(os.environ)
        if not all(env.get(name) for name in self.extra_env):
            return False
        if self.keyless:
            return True
        return bool(self.key_env and env.get(self.key_env))

    def api_key(self, env: dict[str, str] | None = None) -> str | None:
        env = env if env is not None else dict(os.environ)
        if not self.key_env:
            return None
        return env.get(self.key_env) or None

    def model(self, name: str) -> Model | None:
        for m in self.models:
            if m.name == name:
                return m
        return None


@dataclass
class Reply:
    """A normalized successful completion from some provider."""

    text: str
    provider_id: str
    model: str
    raw: dict
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    attempts: int = 1  # how many providers were tried before this one succeeded
    message: dict | None = None  # raw assistant message (carries tool_calls, etc.)
    cached: bool = False  # True if served from the response cache

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.text


@dataclass
class EmbedReply:
    """A normalized embeddings result from some provider."""

    vectors: list[list[float]]
    provider_id: str
    model: str
    prompt_tokens: int | None = None


@dataclass
class TranscribeReply:
    """A normalized audio-transcription (speech→text) result from some provider."""

    text: str
    provider_id: str
    model: str
    raw: dict
    prompt_tokens: int | None = None
