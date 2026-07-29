"""Secret-free, read-only readiness snapshots for the proxy.

The router intentionally still tries cooled or locally exhausted targets as a
last resort. This module provides a more conservative advisory view for
orchestrators and integrations without making provider calls or changing
routing state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

ReadinessStatus = Literal[
    "ready",
    "unconfigured",
    "no_enabled_models",
    "cooldown",
    "quota_exhausted",
]

_STATUSES: tuple[ReadinessStatus, ...] = (
    "ready",
    "unconfigured",
    "no_enabled_models",
    "cooldown",
    "quota_exhausted",
)


class ModelLike(Protocol):
    """Structural model fields needed for readiness."""

    @property
    def name(self) -> str: ...

    @property
    def rpd(self) -> int: ...

    @property
    def enabled(self) -> bool: ...


class ProviderLike(Protocol):
    """Structural provider fields needed for readiness."""

    @property
    def id(self) -> str: ...

    @property
    def models(self) -> Sequence[ModelLike]: ...

    def is_configured(self, env: dict[str, str] | None = None) -> bool: ...


@dataclass(frozen=True)
class ModelReadiness:
    """One enabled model's local readiness state."""

    id: str
    name: str
    ready: bool
    status: ReadinessStatus
    daily_limit: int | None
    used_today: int
    remaining: int | None

    def payload(self) -> dict[str, object]:
        """Return the public, explicitly allow-listed model schema."""
        return {
            "id": self.id,
            "name": self.name,
            "ready": self.ready,
            "status": self.status,
            "daily_limit": self.daily_limit,
            "used_today": self.used_today,
            "remaining": self.remaining,
        }


@dataclass(frozen=True)
class ProviderReadiness:
    """One active Pool provider's local readiness state."""

    id: str
    configured: bool
    ready: bool
    status: ReadinessStatus
    enabled_models: int
    ready_models: int
    cooldown_remaining_s: float
    models: tuple[ModelReadiness, ...]

    def payload(self) -> dict[str, object]:
        """Return the public, explicitly allow-listed provider schema."""
        return {
            "id": self.id,
            "configured": self.configured,
            "ready": self.ready,
            "status": self.status,
            "enabled_models": self.enabled_models,
            "ready_models": self.ready_models,
            "cooldown_remaining_s": self.cooldown_remaining_s,
            "models": [model.payload() for model in self.models],
        }


@dataclass(frozen=True)
class ReadinessSnapshot:
    """A consistent local-capacity snapshot."""

    providers: tuple[ProviderReadiness, ...]

    @property
    def ready_providers(self) -> int:
        return sum(provider.ready for provider in self.providers)

    @property
    def total_providers(self) -> int:
        return len(self.providers)

    @property
    def ready_model_ids(self) -> tuple[str, ...]:
        return tuple(
            model.id
            for provider in self.providers
            for model in provider.models
            if model.ready
        )

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {status: 0 for status in _STATUSES}
        for provider in self.providers:
            counts[provider.status] += 1
        return counts

    def readiness_payload(self) -> dict[str, object]:
        ready = self.ready_providers > 0
        return {
            "schema_version": 1,
            "status": "ready" if ready else "not_ready",
            "reason": "ready_providers_available" if ready else "no_ready_providers",
            "ready_providers": self.ready_providers,
            "total_providers": self.total_providers,
            "summary": self.summary,
        }

    def provider_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "object": "list",
            "data": [provider.payload() for provider in self.providers],
        }


def readiness_snapshot(
    providers: Sequence[ProviderLike],
    *,
    env: Mapping[str, str],
    quota: Mapping[str, int],
    cooldowns: Mapping[str, float],
    route_cooldowns: Mapping[str, float] | None = None,
) -> ReadinessSnapshot:
    """Build a deterministic snapshot without calling an upstream provider."""
    env_copy = dict(env)
    route_cooldowns = route_cooldowns or {}
    rows: list[ProviderReadiness] = []
    for provider in providers:
        configured = provider.is_configured(env_copy)
        enabled = [model for model in provider.models if model.enabled]
        provider_cooldown = max(0.0, float(cooldowns.get(provider.id, 0.0)))
        model_cooldowns = {
            model.name: max(
                provider_cooldown,
                0.0,
                float(route_cooldowns.get(f"{provider.id}/{model.name}", 0.0)),
            )
            for model in enabled
        }
        cooldown = max((provider_cooldown, *model_cooldowns.values()))
        models = tuple(
            _model_readiness(
                provider.id,
                model,
                configured=configured,
                cooled=model_cooldowns[model.name] > 0,
                used=int(quota.get(f"{provider.id}::{model.name}", 0)),
            )
            for model in enabled
        )

        if not configured:
            status: ReadinessStatus = "unconfigured"
        elif not enabled:
            status = "no_enabled_models"
        elif any(model.ready for model in models):
            status = "ready"
        elif any(model.status == "cooldown" for model in models):
            status = "cooldown"
        else:
            status = "quota_exhausted"
        ready_models = sum(model.ready for model in models)
        rows.append(
            ProviderReadiness(
                id=provider.id,
                configured=configured,
                ready=status == "ready",
                status=status,
                enabled_models=len(enabled),
                ready_models=ready_models,
                cooldown_remaining_s=cooldown,
                models=models,
            )
        )
    return ReadinessSnapshot(tuple(rows))


def _model_readiness(
    provider_id: str,
    model: ModelLike,
    *,
    configured: bool,
    cooled: bool,
    used: int,
) -> ModelReadiness:
    daily_limit = model.rpd if model.rpd > 0 else None
    remaining = max(0, daily_limit - used) if daily_limit is not None else None
    if not configured:
        status: ReadinessStatus = "unconfigured"
    elif cooled:
        status = "cooldown"
    elif remaining == 0:
        status = "quota_exhausted"
    else:
        status = "ready"
    return ModelReadiness(
        id=f"{provider_id}/{model.name}",
        name=model.name,
        ready=status == "ready",
        status=status,
        daily_limit=daily_limit,
        used_today=used,
        remaining=remaining,
    )


__all__ = [
    "ModelReadiness",
    "ProviderReadiness",
    "ReadinessSnapshot",
    "ReadinessStatus",
    "readiness_snapshot",
]
