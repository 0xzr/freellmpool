"""Advisory external provider catalog sync.

This imports metadata from mnfst/awesome-free-llm-apis into a local cache. It is
advisory only: the executable provider configuration remains providers.toml.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .toml_utils import toml_escape

DEFAULT_SOURCE_URL = "https://raw.githubusercontent.com/mnfst/awesome-free-llm-apis/main/data.json"

# Cap network reads so a huge/hostile body can't exhaust memory.
_MAX_CATALOG_BYTES = 8 * 1024 * 1024
_MAX_DISCOVER_BYTES = 4 * 1024 * 1024


def _validated_base_url(raw: str, *, https_only: bool, what: str, strip: bool = False) -> str:
    """Validate a base URL before it becomes (or is queried as) a routing target.

    Rejects empty values, embedded whitespace/control characters, and disallowed
    schemes. ``strip=True`` trims surrounding whitespace first (friendly for
    user-typed input); ``strip=False`` validates the raw value (untrusted catalog
    data, so leading/trailing junk is rejected rather than silently normalized).
    """
    url = (raw or "").strip() if strip else (raw or "")
    if not url.strip():
        raise ValueError(f"{what} requires a base URL")
    schemes = ("https://",) if https_only else ("http://", "https://")
    if not url.lower().startswith(schemes) or any(
        c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in url
    ):
        need = "https" if https_only else "http(s)"
        raise ValueError(
            f"{what} has an unsupported base URL (need {need}, no whitespace/control chars)"
        )
    return url


def default_external_catalog_path() -> Path:
    override = os.environ.get("FREELLMPOOL_EXTERNAL_CATALOG_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "freellmpool" / "provider_catalog.json"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never auto-follow redirects — urllib raises HTTPError on a 3xx instead, so a
    request carrying an Authorization header can't forward the key to a redirect
    target (credential leak / SSRF)."""

    def redirect_request(self, *args, **kwargs):  # noqa: D102
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect())


@dataclass(frozen=True)
class ExternalProvider:
    name: str
    slug: str
    category: str | None
    url: str | None
    base_url: str | None
    description: str
    model_count: int
    best_rpd: int
    best_rpm: int
    best_tpd: int
    generous_score: int
    search_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExternalProviderMatch:
    provider: ExternalProvider
    matched: str
    distance: int
    exact: bool


def sync_external_catalog(
    *,
    source_url: str = DEFAULT_SOURCE_URL,
    path: Path | None = None,
    timeout: float = 20.0,
) -> tuple[Path, list[ExternalProvider]]:
    path = path or default_external_catalog_path()
    # Validate the source: https-only, no file:// / SSRF even from a caller override.
    source_url = _validated_base_url(source_url, https_only=True, what="external catalog")
    # Do not follow redirects: a caller-provided public URL must not be able to
    # bounce this server-side fetch to a private network target.
    request = urllib.request.Request(source_url, headers={"Accept": "application/json"})
    with _NO_REDIRECT_OPENER.open(request, timeout=timeout) as response:
        raw_bytes = response.read(_MAX_CATALOG_BYTES + 1)
    if len(raw_bytes) > _MAX_CATALOG_BYTES:
        raise ValueError(f"external catalog exceeds {_MAX_CATALOG_BYTES} bytes; refusing to load")
    data = json.loads(raw_bytes.decode("utf-8"))
    providers = parse_external_catalog(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return path, providers


def load_external_catalog(path: Path | None = None) -> list[ExternalProvider]:
    path = path or default_external_catalog_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []
    return parse_external_catalog(data)


def parse_external_catalog(data: Any) -> list[ExternalProvider]:
    rows = data.get("providers", []) if isinstance(data, dict) else []
    providers = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        models = row.get("models") if isinstance(row.get("models"), list) else []
        best_rpd = 0
        best_rpm = 0
        best_tpd = 0
        for model in models:
            if not isinstance(model, dict):
                continue
            limit = str(model.get("rateLimit") or "")
            best_rpd = max(best_rpd, _extract_limit(limit, "RPD"))
            best_rpm = max(best_rpm, _extract_limit(limit, "RPM"))
            best_tpd = max(best_tpd, _extract_limit(limit, "TPD"))
        score = best_tpd or best_rpd or best_rpm or 0
        providers.append(
            ExternalProvider(
                name=name,
                slug=_slug(name),
                category=_optional(row.get("category")),
                url=_optional(row.get("url")),
                base_url=_optional(row.get("baseUrl")),
                description=str(row.get("description") or ""),
                model_count=len(models),
                best_rpd=best_rpd,
                best_rpm=best_rpm,
                best_tpd=best_tpd,
                generous_score=score,
                search_terms=tuple(
                    str(model.get("id") or model.get("name") or "").strip()
                    for model in models
                    if isinstance(model, dict) and (model.get("id") or model.get("name"))
                ),
            )
        )
    providers.sort(key=lambda p: (-p.generous_score, p.slug))
    return providers


def _optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _extract_model_rpd(text: str) -> int:
    pattern = re.compile(r"([0-9][0-9,.]*\s*[kKmMbB]?)\s*RPD")
    values = [_parse_number(match.group(1)) for match in pattern.finditer(text)]
    values = [value for value in values if value > 0]
    if not values:
        return 0
    return min(values)


def _extract_limit(text: str, unit: str) -> int:
    pattern = re.compile(r"([0-9][0-9,.]*\s*[kKmMbB]?)\s*" + re.escape(unit))
    values = [_parse_number(match.group(1)) for match in pattern.finditer(text)]
    return max(values) if values else 0


def _parse_number(value: str) -> int:
    text = value.strip().replace(",", "")
    multiplier = 1
    if text[-1:].lower() == "k":
        multiplier = 1_000
        text = text[:-1]
    elif text[-1:].lower() == "m":
        multiplier = 1_000_000
        text = text[:-1]
    elif text[-1:].lower() == "b":
        multiplier = 1_000_000_000
        text = text[:-1]
    try:
        return int(float(text.strip()) * multiplier)
    except ValueError:
        return 0


def match_local_provider(external: ExternalProvider, local_providers) -> str | None:
    """Return the local provider id matching an external catalog row, if any."""
    external_base = (external.base_url or "").rstrip("/").lower()
    for provider in local_providers:
        local_base = (provider.base_url or "").rstrip("/").lower()
        if local_base == external_base and external_base:
            return provider.id
    external_slug = external.slug
    for provider in local_providers:
        if _slug(provider.id) == external_slug or _slug(provider.label) == external_slug:
            return provider.id
    aliases = {
        "google-gemini": "gemini",
        "github-models": "github",
        "z-ai-zhipu-glm": "zhipu",
        "llm7-io": "llm7",
        "openrouter": "openrouter",
        "nvidia-nim": "nvidia",
        "groq": "groq",
        "cerebras": "cerebras",
        "cohere": "cohere",
        "mistral-ai": "mistral",
        "sambanova": "sambanova",
        "cloudflare-workers-ai": "cloudflare",
        "ollama-cloud": "ollama",
    }
    wanted = aliases.get(external_slug)
    if wanted and any(provider.id == wanted for provider in local_providers):
        return wanted
    return None


def suggest_external_provider(
    query: str, providers: list[ExternalProvider]
) -> ExternalProviderMatch | None:
    """Return the best external provider match from provider names or model ids."""
    needle = _external_lookup_slug(query)
    if not needle:
        return None

    candidates: list[tuple[ExternalProvider, str]] = []
    for provider in providers:
        candidates.append((provider, provider.name))
        candidates.append((provider, provider.slug))
        for model in provider.search_terms:
            candidates.append((provider, model))

    best: ExternalProviderMatch | None = None
    for provider, value in candidates:
        candidate = _external_lookup_slug(value)
        if not candidate:
            continue
        distance = _levenshtein(needle, candidate)
        exact = distance == 0 or needle in candidate or candidate in needle
        if exact:
            distance = 0
        if best is None or distance < best.distance:
            best = ExternalProviderMatch(
                provider=provider, matched=value, distance=distance, exact=exact
            )

    if best is None:
        return None
    max_distance = max(2, len(needle) // 4)
    return best if best.exact or best.distance <= max_distance else None


def import_external_provider_to_user_catalog(query: str) -> str:
    """Create a user providers.toml stub from an external-only catalog provider.

    Returns the local provider id that was written. This is intentionally a
    user-catalog append, not a change to the packaged providers.toml.
    """
    data_path = default_external_catalog_path()
    try:
        raw = json.loads(data_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        raise ValueError(
            "external catalog cache is missing; run freellmpool catalog sync or "
            "freellmpool capacity status first"
        ) from None
    match = _find_external_provider_row(raw, query)
    if match is None:
        rows = raw.get("providers", []) if isinstance(raw, dict) else []
        available = ", ".join(str(row.get("name")) for row in rows[:8] if isinstance(row, dict))
        raise ValueError(
            f"provider not found in external catalog: {query}. Try one of: {available}"
        )
    # Third-party catalog data; the imported provider becomes an executable
    # routing target once its key is set, so validate the RAW value strictly.
    base_url = _validated_base_url(
        str(match.get("baseUrl") or ""), https_only=True, what=f"external provider {query}"
    )
    provider_id = _slug(str(match.get("name") or query)).replace("-", "_")
    if not provider_id:
        raise ValueError(f"external provider name does not produce a valid id: {query}")
    key_env = provider_id.upper() + "_API_KEY"
    models = []
    for model in match.get("models", []):
        if not isinstance(model, dict):
            continue
        model_id = model.get("id") or model.get("name")
        if not model_id:
            continue
        model_id = str(model_id).strip()
        if not model_id or model_id.startswith("+"):
            continue
        modality = str(model.get("modality") or "").lower()
        if "image" in modality and "text" not in modality:
            continue
        limit = str(model.get("rateLimit") or "")
        rpd = _extract_model_rpd(limit)
        models.append((model_id, rpd))
    if not models:
        raise ValueError(f"external provider has no usable text models: {query}")
    path = _user_provider_catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if f'id = "{provider_id}"' not in existing:
        with path.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write("\n[[provider]]\n")
            fh.write(f'id = "{provider_id}"\n')
            fh.write(f'label = "{toml_escape(str(match.get("name") or query))}"\n')
            fh.write('adapter = "openai"\n')
            fh.write(f'base_url = "{toml_escape(base_url)}"\n')
            fh.write(f'key_env = "{key_env}"\n')
            fh.write("models = [\n")
            for model_id, rpd in models:
                fh.write(f'    {{ name = "{toml_escape(model_id)}", rpd = {rpd} }},\n')
            fh.write("]\n")
    return provider_id


def create_user_provider_stub(
    *,
    name: str,
    base_url: str,
    model: str,
    key_env: str | None = None,
) -> str:
    """Append a minimal OpenAI-compatible provider to the user providers.toml."""
    provider_id = _slug(name).replace("-", "_")
    if not provider_id:
        raise ValueError("provider name is required")
    base_url = _validated_base_url(base_url, https_only=False, what="provider", strip=True).rstrip(
        "/"
    )
    model = model.strip()
    if not model:
        raise ValueError("model is required")
    key_env = key_env or provider_id.upper() + "_API_KEY"

    path = _user_provider_catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if f'id = "{provider_id}"' not in existing:
        with path.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write("\n[[provider]]\n")
            fh.write(f'id = "{provider_id}"\n')
            fh.write(f'label = "{toml_escape(name)}"\n')
            fh.write('adapter = "openai"\n')
            fh.write(f'base_url = "{toml_escape(base_url)}"\n')
            fh.write(f'key_env = "{toml_escape(key_env)}"\n')
            fh.write("models = [\n")
            fh.write(f'    {{ name = "{toml_escape(model)}" }},\n')
            fh.write("]\n")
    return provider_id


def discover_openai_models(
    base_url: str,
    *,
    api_key: str | None = None,
    timeout: float = 10.0,
) -> list[str]:
    """Discover model ids from an OpenAI-compatible /models endpoint."""
    # base_url is user-supplied; only fetch http(s) (never file://, etc.).
    url = (
        _validated_base_url(base_url, https_only=False, what="model discovery", strip=True).rstrip(
            "/"
        )
        + "/models"
    )
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers)
    try:
        # No-redirect opener: never forward the Bearer key to a 3xx target.
        with _NO_REDIRECT_OPENER.open(request, timeout=timeout) as response:
            raw_bytes = response.read(_MAX_DISCOVER_BYTES + 1)
        if len(raw_bytes) > _MAX_DISCOVER_BYTES:
            raise ValueError(f"model discovery response exceeds {_MAX_DISCOVER_BYTES} bytes")
        data = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"model discovery failed: {exc}") from None
    rows = data.get("data", []) if isinstance(data, dict) else []
    models = []
    for row in rows:
        model_id = row.get("id") if isinstance(row, dict) else None
        if model_id:
            models.append(str(model_id))
    return models


def _external_lookup_slug(value: str) -> str:
    slug = _slug(value)
    aliases = {
        "ai21": "ai21-labs",
        "ai21labs": "ai21-labs",
        "model-scope": "modelscope",
        "modelscope": "modelscope",
        "silicon-flow": "siliconflow",
        "google": "google-gemini",
        "gemini": "google-gemini",
        "github": "github-models",
        "hf": "hugging-face",
        "huggingface": "hugging-face",
    }
    return aliases.get(slug, slug)


def _find_external_provider_row(data: Any, query: str) -> dict | None:
    needle = _external_lookup_slug(query)
    rows = data.get("providers", []) if isinstance(data, dict) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if _external_lookup_slug(name) == needle:
            return row
    return None


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for i, char_left in enumerate(left, start=1):
        current = [i]
        for j, char_right in enumerate(right, start=1):
            cost = 0 if char_left == char_right else 1
            current.append(
                min(
                    current[j - 1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + cost,
                )
            )
        previous = current
    return previous[-1]


def _user_provider_catalog_path() -> Path:
    """Return the providers.toml path used for user provider definitions."""
    override = os.environ.get("FREELLMPOOL_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "freellmpool" / "providers.toml"
