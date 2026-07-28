"""Capacity reporting for configured free-tier providers.

This module is deliberately read-only: it summarizes catalog/env/quota state so
users can keep several legitimate providers available without automating signup
or account creation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .config import configured_providers, effective_env, load_catalog
from .key_inventory import KeyRecord, records_by_provider
from .models import Provider
from .quota import QuotaStore


@dataclass(frozen=True)
class ProviderCapacity:
    provider_id: str
    label: str
    status: str
    reason: str
    key_env: str | None
    configured: bool
    keyless: bool
    enabled_models: int
    total_models: int
    used_today: int
    quota_hint: int
    inventory_count: int = 0
    expires_at: str | None = None

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"


@dataclass(frozen=True)
class CapacityReport:
    providers: list[ProviderCapacity]
    target: int = 5

    @property
    def healthy_count(self) -> int:
        return sum(1 for p in self.providers if p.healthy)

    @property
    def low_quota_count(self) -> int:
        return sum(1 for p in self.providers if p.status == "low_quota")

    @property
    def needs_action(self) -> bool:
        return self.healthy_count < self.target

    def checklist(self) -> list[ProviderCapacity]:
        missing = [p for p in self.providers if p.status == "missing" and p.key_env]
        missing.sort(key=lambda p: (p.inventory_count, p.provider_id))
        return missing[: max(0, self.target - self.healthy_count)]


def build_capacity_report(
    *,
    target: int = 5,
    env: dict[str, str] | None = None,
    quota: QuotaStore | None = None,
    catalog: list[Provider] | None = None,
    inventory: list[KeyRecord] | None = None,
) -> CapacityReport:
    env = effective_env(env)
    catalog = catalog or load_catalog()
    quota = quota or QuotaStore()
    inventory = inventory or []
    inv = records_by_provider(inventory)
    configured_ids = {p.id for p in configured_providers(catalog, env)}
    snap = quota.snapshot()
    rows = [
        _provider_capacity(provider, configured_ids, snap, inv.get(provider.id, []))
        for provider in catalog
    ]
    rows.sort(key=_capacity_sort_key)
    return CapacityReport(providers=rows, target=target)


def _provider_capacity(
    provider: Provider,
    configured_ids: set[str],
    snap: dict[str, int],
    inventory: list[KeyRecord],
) -> ProviderCapacity:
    configured = provider.id in configured_ids
    enabled_models = sum(1 for m in provider.models if m.enabled)
    used_today = sum(int(v) for k, v in snap.items() if k.startswith(f"{provider.id}::"))
    quota_hint = sum(m.rpd for m in provider.models if m.enabled and m.rpd > 0)
    expires_at = _soonest_expiry(inventory)

    if not configured:
        status = "missing"
        if provider.key_env:
            reason = f"set {provider.key_env}"
        elif provider.extra_env:
            reason = "missing extra environment values"
        else:
            reason = "not configured"
    elif quota_hint > 0 and used_today >= quota_hint:
        status = "exhausted"
        reason = "daily quota hint reached"
    elif quota_hint > 0 and used_today >= quota_hint * 0.8:
        status = "low_quota"
        reason = "usage is above 80% of daily quota hint"
    elif expires_at and _is_expired_or_today(expires_at):
        status = "invalid_key"
        reason = "inventory expiry date has passed or is today"
    else:
        status = "healthy"
        reason = "configured and usable"

    return ProviderCapacity(
        provider_id=provider.id,
        label=provider.label,
        status=status,
        reason=reason,
        key_env=provider.key_env,
        configured=configured,
        keyless=provider.keyless,
        enabled_models=enabled_models,
        total_models=len(provider.models),
        used_today=used_today,
        quota_hint=quota_hint,
        inventory_count=len(inventory),
        expires_at=expires_at,
    )


def _capacity_sort_key(row: ProviderCapacity) -> tuple[int, int, int, str]:
    # Higher local quota hints and more enabled models indicate more useful capacity.
    generosity = row.quota_hint if row.quota_hint > 0 else 0
    return (_status_rank(row.status), -generosity, -row.enabled_models, row.provider_id)


def _status_rank(status: str) -> int:
    order = {
        "healthy": 0,
        "low_quota": 1,
        "exhausted": 2,
        "invalid_key": 3,
        "missing": 4,
        "disabled": 5,
    }
    return order.get(status, 9)


def _soonest_expiry(records: list[KeyRecord]) -> str | None:
    values: list[date] = []
    for record in records:
        if not record.expires_at:
            continue
        try:
            values.append(date.fromisoformat(record.expires_at))
        except ValueError:
            continue
    return min(values).isoformat() if values else None


def _is_expired_or_today(value: str) -> bool:
    try:
        return date.fromisoformat(value) <= date.today()
    except ValueError:
        return False
