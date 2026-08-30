"""Configuration loading: provider catalog + user overrides.

Resolution order for the provider catalog:

1. The packaged ``providers.toml`` (the built-in catalog).
2. A user catalog at ``$FREELLMPOOL_CONFIG`` or
   ``~/.config/freellmpool/providers.toml`` if present. Providers with the same
   ``id`` override the built-ins; new ids are appended.

Only providers whose API key (and any extra env vars) are present in the
environment are returned by :func:`configured_providers`.
"""

from __future__ import annotations

import copy
import ipaddress
import logging
import os
import re
import socket
import tomllib
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from .models import Model, Provider

_PACKAGED_CATALOG = Path(__file__).with_name("providers.toml")
_log = logging.getLogger("freellmpool")

# Control characters (incl. CR/LF/TAB) are never valid in a base_url, provider id,
# or model name — they enable response-header injection (those values are echoed
# into X-Freellmpool-* headers) and request smuggling.
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_TOML_LOCATION_RE = re.compile(r"at line (\d+), column (\d+)")


def _allow_local_providers() -> bool:
    """Opt-in (FREELLMPOOL_ALLOW_LOCAL_PROVIDERS) to permit loopback/private
    base_urls — for users who deliberately run self-hosted providers (Ollama,
    LM Studio, a LAN gateway)."""
    return os.environ.get("FREELLMPOOL_ALLOW_LOCAL_PROVIDERS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _safe_name(value: str) -> bool:
    return bool(value) and _CTRL_RE.search(value) is None


def _safe_base_url(url: str, *, allow_local: bool) -> bool:
    """Reject base_urls that would turn a configured provider key into an SSRF /
    key-exfil vector: non-http(s) schemes, embedded credentials, control chars, and
    (unless opted in) loopback / private / link-local / reserved targets. A bare
    hostname that isn't a literal private IP is allowed (public DNS); DNS-rebinding
    is out of scope for a parse-time check."""
    if not url or _CTRL_RE.search(url) or any(c.isspace() for c in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        return False
    host = parts.hostname
    # Non-ASCII hosts (fullwidth digits/dots, unicode look-alikes like "ⓛocalhost")
    # and percent-encoded hosts are IDNA/resolver-normalized to a real target at
    # connect time and can map to loopback — legit provider hosts are plain ASCII.
    if not host or not host.isascii() or "%" in host or "\\" in host:
        return False
    if allow_local:
        return True
    host = host.rstrip(".")  # a trailing dot (FQDN root) must not bypass the checks
    low = host.lower()
    if not low or low == "localhost" or low.endswith((".local", ".internal", ".localhost")):
        return False
    # Canonicalize the host to a literal IP if it is one in ANY form a resolver
    # accepts — dotted, but also decimal (2130706433), hex (0x7f000001), octal, and
    # short forms (127.1) — so those can't smuggle a loopback/private target past us.
    candidate = host
    try:
        candidate = socket.inet_ntoa(socket.inet_aton(host))
    except OSError:
        pass
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        return True  # a real hostname, not any literal-IP form
    # An IPv4-mapped IPv6 (::ffff:127.0.0.1) reflects the embedded IPv4's reachability;
    # normalize so the check is correct on every Python, not only 3.11+ (bpo-46203).
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
    )


def _safe_local_catalog_url(url: str) -> bool:
    """True only for a canonical literal loopback URL trusted by local import.

    ``local = true`` is intentionally narrower than the legacy environment opt-in:
    it cannot authorize localhost DNS, LAN/private addresses, alternate IPv4
    spellings, or IPv4-mapped IPv6 addresses.
    """
    if not url or _CTRL_RE.search(url) or any(c.isspace() for c in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if (
        parts.scheme not in ("http", "https")
        or parts.username
        or parts.password
        or not parts.hostname
    ):
        return False
    host = parts.hostname
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None) is not None or not ip.is_loopback:
        return False
    # Reject resolver-compatible shorthand/non-canonical text such as 127.1 and
    # verbose IPv6. urlsplit removes brackets, so ::1 is the one canonical v6 form.
    return host == str(ip)


# Common OpenAI / Anthropic model names mapped to a free target, so existing
# code (which hardcodes e.g. "gpt-4o-mini") works against freellmpool unchanged.
# "auto" means "let the pool pick the least-used free provider". Override or add
# your own with env vars, e.g.  FREELLMPOOL_ALIAS_gpt-4o-mini=groq/llama-3.3-70b-versatile
_DEFAULT_ALIASES: dict[str, str] = {
    "gpt-4o-mini": "auto",
    "gpt-4o": "auto",
    "gpt-4.1": "auto",
    "gpt-4.1-mini": "auto",
    "gpt-4.1-nano": "auto",
    "gpt-4-turbo": "auto",
    "gpt-4": "auto",
    "gpt-3.5-turbo": "auto",
    "o1-mini": "auto",
    "o3-mini": "auto",
    "o4-mini": "auto",
    "claude-3-haiku-20240307": "auto",
    "claude-3-5-haiku-latest": "auto",
    "claude-3-5-sonnet-latest": "auto",
    "claude-3-7-sonnet-latest": "auto",
    "claude-3-opus-latest": "auto",
}

_ALIAS_ENV_PREFIX = "FREELLMPOOL_ALIAS_"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def resolve_alias(name: str, env: dict[str, str] | None = None) -> str:
    """Map a well-known model name to its free target. User env overrides win;
    unknown names pass through unchanged."""
    env = env if env is not None else dict(os.environ)
    target = _norm(name)
    # Sorted so that when two env vars normalize to the same alias, the winner is
    # deterministic rather than dict-iteration-order dependent.
    for key, value in sorted(env.items()):
        if key.startswith(_ALIAS_ENV_PREFIX) and _norm(key[len(_ALIAS_ENV_PREFIX) :]) == target:
            return value or name
    cfg_aliases = load_config_file(env).get("aliases", {})
    if not isinstance(cfg_aliases, dict):
        cfg_aliases = {}
    if name in cfg_aliases:
        return str(cfg_aliases[name])
    if name in _DEFAULT_ALIASES:
        return _DEFAULT_ALIASES[name]
    # Prefix fallback: any unknown OpenAI/Anthropic frontier name routes to a free
    # model, so e.g. Claude Code's "claude-sonnet-4-..." just works.
    low = name.lower()
    if low.startswith(("claude-", "claude ", "gpt-", "o1-", "o3-", "o4-", "chatgpt")):
        return "auto"
    return name


def split_provider_model(
    requested: str, provider_ids: set[str] | None
) -> tuple[list[str] | None, str | None]:
    """Split ``provider/model`` into ``([provider], model)`` — but ONLY when the prefix is a
    real provider id. Otherwise the whole string is a model name that legitimately contains a
    slash (OpenRouter / Hugging Face / Kilo ids like ``Qwen/Qwen3-Coder`` or
    ``deepseek-ai/DeepSeek-R1``), so it must not be mis-split into provider ``Qwen``."""
    if requested and provider_ids and "/" in requested:
        prov, _, mdl = requested.partition("/")
        if prov in provider_ids:
            return [prov], mdl
    return None, requested


def known_aliases(env: dict[str, str] | None = None) -> list[str]:
    """Model aliases understood by :func:`resolve_alias`.

    Used by gateway model discovery so clients can choose a well-known Claude or
    OpenAI model name and still have the proxy resolve it to the free pool.
    """
    env = env if env is not None else dict(os.environ)
    return list(_known_aliases_cached(_alias_cache_key(env)))


def _alias_cache_key(env: dict[str, str]) -> tuple:
    """Stable cache key for alias discovery.

    Only alias-related env vars and config-file path metadata affect
    ``known_aliases``. File mtime/size keep gateway discovery fresh after config
    edits without re-reading TOML on every `/v1/models` request.
    """
    path = _config_file_path(env)
    config_sig = _path_signature(path)
    env_aliases = tuple(sorted((k, v) for k, v in env.items() if k.startswith(_ALIAS_ENV_PREFIX)))
    return config_sig, env_aliases


# LRU eviction is fine here: a dropped entry recomputes from env/config metadata.
@lru_cache(maxsize=64)
def _known_aliases_cached(cache_key: tuple) -> tuple[str, ...]:
    config_sig, env_aliases = cache_key
    path_str = config_sig[0]
    aliases = set(_DEFAULT_ALIASES)
    if path_str:
        cfg = load_config_file({"FREELLMPOOL_CONFIG_FILE": path_str})
        aliases.update(str(k) for k in cfg.get("aliases", {}))
    aliases.update(k[len(_ALIAS_ENV_PREFIX) :] for k, _ in env_aliases)
    return tuple(sorted(aliases))


def _user_catalog_path() -> Path | None:
    override = os.environ.get("FREELLMPOOL_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "freellmpool" / "providers.toml"


def _config_file_path(env: dict[str, str]) -> Path | None:
    override = env.get("FREELLMPOOL_CONFIG_FILE")
    if override:
        return Path(override).expanduser()
    default = Path.home() / ".config" / "freellmpool" / "config.toml"
    return default if default.exists() else None


def _path_signature(path: Path | None) -> tuple:
    """Full cache identity, including create/delete and atomic replacement."""
    if path is None:
        return ("", False, 0, 0, 0, 0, 0, 0)
    normalized = path.expanduser().resolve(strict=False)
    try:
        stat = normalized.stat()
    except OSError:
        return (str(normalized), False, 0, 0, 0, 0, 0, 0)
    return (
        str(normalized),
        True,
        int(getattr(stat, "st_dev", 0)),
        int(getattr(stat, "st_ino", 0)),
        int(getattr(stat, "st_mode", 0)),
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(getattr(stat, "st_ctime_ns", 0)),
    )


@lru_cache(maxsize=128)
def _read_toml_cached(signature: tuple) -> tuple[dict, tuple | None]:
    """Parse one immutable path/stat version, retaining sanitized error metadata."""
    path_str, exists, *_ = signature
    if not path_str or not exists:
        return {}, None
    path = Path(path_str)
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        return (data if isinstance(data, dict) else {}), None
    except tomllib.TOMLDecodeError as exc:
        message = str(exc)
        match = _TOML_LOCATION_RE.search(message)
        line = int(match.group(1)) if match else None
        column = int(match.group(2)) if match else None
        if line is None or column is None:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                text = ""
            lines = text.splitlines() or [""]
            line = len(lines)
            column = len(lines[-1]) + 1
        return {}, ("toml_syntax", line, column)
    except OSError:
        return {}, ("config_unreadable", None, None)


def load_config_file(env: dict[str, str] | None = None) -> dict:
    """Load the optional config.toml. Returns {} if none exists.

    Recognized tables:
        [keys]      PROVIDER_API_KEY = "..."   (provider key env vars)
        [aliases]   "gpt-4o-mini" = "auto"     (model name -> free target)
        [settings]  cooldown_seconds = 60, proxy_key = "...", host/port
    """
    env = env if env is not None else dict(os.environ)
    path = _config_file_path(env)
    if path is None:
        return {}
    data, error = _read_toml_cached(_path_signature(path))
    return {} if error is not None else copy.deepcopy(data)


def config_diagnostics(env: dict[str, str] | None = None) -> list[dict[str, object]]:
    """Strict, secret-safe diagnostics for the otherwise tolerant config loader."""
    env = env if env is not None else dict(os.environ)
    path = _config_file_path(env)
    if path is None:
        return []
    signature = _path_signature(path)
    data, error = _read_toml_cached(signature)
    if error is not None:
        code, line, column = error
        message = (
            "config.toml contains invalid TOML syntax"
            if code == "toml_syntax"
            else "config.toml could not be read"
        )
        return [
            {
                "code": code,
                "message": message,
                "path": signature[0],
                "line": line,
                "column": column,
            }
        ]
    diagnostics: list[dict[str, object]] = []
    for table in ("keys", "settings", "aliases"):
        if table in data and not isinstance(data[table], dict):
            diagnostics.append(
                {
                    "code": "table_type",
                    "message": f"[{table}] must be a table",
                    "path": signature[0],
                    "table": table,
                    "line": None,
                    "column": None,
                }
            )
    return diagnostics


def effective_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Real environment with config-file ``[keys]`` filled in underneath, so
    actual env vars always win but config.toml provides defaults."""
    env = env if env is not None else dict(os.environ)
    keys = load_config_file(env).get("keys", {})
    if not isinstance(keys, dict):
        keys = {}
    merged = {str(k): str(v) for k, v in keys.items() if v}
    merged.update(env)
    return merged


def settings(env: dict[str, str] | None = None) -> dict:
    """The ``[settings]`` table from config.toml (or {})."""
    value = load_config_file(env).get("settings", {})
    return value if isinstance(value, dict) else {}


def _maybe_int(value, *, positive: bool = False) -> int | None:
    """Best-effort int from possibly-bad input; None on failure (and, when
    ``positive``, on a non-positive value — so ``context = 0`` reads as unknown)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if positive and n <= 0:
        return None
    return n


def _parse_rows(rows: list, *, allow_local: bool | None = None) -> list[Provider]:
    """Parse provider rows tolerantly: a malformed row (missing id/base_url/name,
    bad int) is skipped, not fatal, so one typo in a user catalog can't brick the
    whole tool. The packaged catalog is valid, so this is a no-op for it."""
    providers: list[Provider] = []
    allow_local = _allow_local_providers() if allow_local is None else allow_local
    for row in rows:
        if not isinstance(row, dict) or not row.get("id") or not row.get("base_url"):
            continue
        provider_id = str(row["id"])
        base_url = str(row["base_url"]).rstrip("/")
        local_catalog = row.get("local") is True
        # Security: a bad base_url turns this provider's API key into an SSRF /
        # key-exfil POST; a control char in the id/name injects response headers.
        # Drop the offending row (tolerant, like a malformed row) and warn.
        if not _safe_name(provider_id):
            _log.warning("skipping provider with unsafe id %r", provider_id)
            continue
        if local_catalog:
            safe_url = _safe_local_catalog_url(base_url)
        else:
            safe_url = _safe_base_url(base_url, allow_local=allow_local)
        if not safe_url:
            _log.warning("skipping provider %s: unsafe base_url %r", provider_id, base_url)
            continue
        models = []
        for m in row.get("models", []):
            if not isinstance(m, dict) or not m.get("name"):
                continue
            model_name = str(m["name"])
            if not _safe_name(model_name):
                _log.warning("skipping model with unsafe name %r on %s", model_name, provider_id)
                continue
            models.append(
                Model(
                    name=model_name,
                    rpd=_maybe_int(m.get("rpd", 0)) or 0,
                    enabled=bool(m.get("enabled", True)),
                    auto=bool(m.get("auto", True)),
                    context=_maybe_int(m.get("context"), positive=True),
                )
            )
        key_env = row.get("key_env")
        extra_env = row.get("extra_env", [])
        env_names = ([key_env] if key_env is not None else []) + (
            list(extra_env) if isinstance(extra_env, (list, tuple)) else []
        )
        if not isinstance(extra_env, (list, tuple)) or any(
            not isinstance(name, str) or _ENV_NAME_RE.fullmatch(name) is None
            for name in env_names
        ):
            _log.warning("skipping provider %s: unsafe environment variable name", provider_id)
            continue
        providers.append(
            Provider(
                id=provider_id,
                label=str(row.get("label", row["id"])),
                adapter=str(row.get("adapter", "openai")),
                base_url=base_url,
                key_env=key_env,
                auth=str(row.get("auth", "bearer")),
                key_optional=bool(row.get("key_optional", False)),
                models=tuple(models),
                extra_env=tuple(extra_env),
            )
        )
    return providers


def _parse_catalog(data: dict) -> list[Provider]:
    return _parse_rows(data.get("provider", []))


@lru_cache(maxsize=128)
def _parsed_section_cached(
    signature: tuple, section: str, allow_local: bool
) -> tuple[Provider, ...]:
    data, error = _read_toml_cached(signature)
    if error is not None:
        return ()
    rows = data.get(section, []) if isinstance(data, dict) else []
    return tuple(_parse_rows(rows if isinstance(rows, list) else [], allow_local=allow_local))


def load_embedders(path: Path | None = None) -> list[Provider]:
    """Load the embedder catalog ([[embedder]] rows). Same shape as providers."""
    base_path = path or _PACKAGED_CATALOG
    return list(
        _parsed_section_cached(
            _path_signature(base_path), "embedder", _allow_local_providers()
        )
    )


def configured_embedders(
    catalog: list[Provider] | None = None, env: dict[str, str] | None = None
) -> list[Provider]:
    catalog = catalog if catalog is not None else load_embedders()
    env = env if env is not None else dict(os.environ)
    return [p for p in catalog if p.is_configured(env)]


def load_transcribers(path: Path | None = None) -> list[Provider]:
    """Load the transcriber catalog ([[transcriber]] rows). Same shape as providers —
    audio→text (Whisper) endpoints on the OpenAI /audio/transcriptions surface."""
    base_path = path or _PACKAGED_CATALOG
    return list(
        _parsed_section_cached(
            _path_signature(base_path), "transcriber", _allow_local_providers()
        )
    )


def configured_transcribers(
    catalog: list[Provider] | None = None, env: dict[str, str] | None = None
) -> list[Provider]:
    catalog = catalog if catalog is not None else load_transcribers()
    env = env if env is not None else dict(os.environ)
    return [p for p in catalog if p.is_configured(env)]


def load_catalog(path: Path | None = None) -> list[Provider]:
    """Load the full provider catalog (built-ins + user overrides)."""
    base_path = path or _PACKAGED_CATALOG
    allow_local = _allow_local_providers()
    providers = list(
        _parsed_section_cached(_path_signature(base_path), "provider", allow_local)
    )

    if path is None:
        user_path = _user_catalog_path()
        if user_path is not None:
            user_providers = list(
                _parsed_section_cached(
                    _path_signature(user_path), "provider", allow_local
                )
            )
            by_id = {p.id: p for p in providers}
            for up in user_providers:
                by_id[up.id] = up
            providers = list(by_id.values())

    return providers


def configured_providers(
    catalog: list[Provider] | None = None,
    env: dict[str, str] | None = None,
) -> list[Provider]:
    """Return only providers that have a usable API key in the environment."""
    catalog = catalog if catalog is not None else load_catalog()
    env = env if env is not None else effective_env()
    return [p for p in catalog if p.is_configured(env)]
