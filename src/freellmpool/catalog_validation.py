"""Local catalog validation used by CI and ``freellmpool doctor``."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .capability import capability_table, model_capability
from .config import _safe_local_catalog_url, load_catalog, load_embedders, load_transcribers
from .models import Provider

_ADAPTERS = {"openai", "gemini", "cloudflare"}
_AUTH = {"bearer", "none"}
_MAX_MODEL_ID = 200
_SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9._:/@+-]{0,199}$")


def normalize_model_listing(payload: Any) -> tuple[str, ...]:
    """Return bounded, safe canonical model names and aliases from common listings."""

    if isinstance(payload, dict):
        rows = payload.get("data")
        if rows is None:
            rows = payload.get("models")
    else:
        rows = payload
    if not isinstance(rows, list):
        return ()
    found: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            candidates: list[Any] = [row.get("id") or row.get("name")]
            aliases = row.get("aliases")
            if isinstance(aliases, list):
                candidates.extend(aliases)
        else:
            candidates = [row]
        for model_id in candidates:
            if (
                isinstance(model_id, str)
                and 1 <= len(model_id) <= _MAX_MODEL_ID
                and _SAFE_MODEL_ID.fullmatch(model_id)
            ):
                found.add(model_id)
    return tuple(sorted(found))


def _valid_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and not any(ch in value for ch in "\r\n\t")
    ) or _safe_local_catalog_url(value)


def _check_group(name: str, providers: list[Provider]) -> list[str]:
    errors: list[str] = []
    ids = Counter(p.id for p in providers)
    for provider_id, count in ids.items():
        if count > 1:
            errors.append(f"{name}: duplicate provider id {provider_id!r}")
    for provider in providers:
        prefix = f"{name}:{provider.id}"
        if provider.adapter not in _ADAPTERS:
            errors.append(f"{prefix}: unsupported adapter {provider.adapter!r}")
        if provider.auth not in _AUTH:
            errors.append(f"{prefix}: unsupported auth {provider.auth!r}")
        if not _valid_url(provider.base_url):
            errors.append(
                f"{prefix}: base_url must be https or canonical literal loopback "
                "without control chars"
            )
        if not provider.models:
            errors.append(f"{prefix}: no models configured")
        model_names = Counter(model.name for model in provider.models)
        for model_name, count in model_names.items():
            if count > 1:
                errors.append(f"{prefix}: duplicate model {model_name!r}")
        for model in provider.models:
            if model.rpd < 0:
                errors.append(f"{prefix}/{model.name}: rpd must be non-negative")
            if model.context is not None and model.context <= 0:
                errors.append(f"{prefix}/{model.name}: context must be positive")
    return errors


def validate_catalog(path: Path | None = None) -> list[str]:
    """Validate provider, embedder, and transcriber rows from ``path`` or bundled catalog."""
    providers = load_catalog(path)
    table = capability_table()
    errors = [
        *_check_group("provider", providers),
        *_check_group("embedder", load_embedders(path)),
        *_check_group("transcriber", load_transcribers(path)),
    ]
    for provider in providers:
        for model in provider.models:
            score = model_capability(model.name, table)
            if not 0.0 <= score <= 1.0:
                errors.append(f"provider:{provider.id}/{model.name}: invalid capability score {score}")
    return errors
