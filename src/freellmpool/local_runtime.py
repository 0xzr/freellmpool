"""Explicit, loopback-only discovery and import for local OpenAI runtimes.

This module deliberately does not scan the LAN, inspect processes, read runtime
credentials, or enable a route automatically.  Discovery is a bounded GET to a
small known list (or one explicit literal-loopback URL); import creates pin-only
user-catalog rows marked for reversible removal.
"""

from __future__ import annotations

import errno
import http.client
import ipaddress
import json
import os
import re
import stat
import tempfile
import tomllib
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .toml_utils import toml_escape

_MAX_BODY_BYTES = 1024 * 1024
_MAX_MODELS = 256
_MAX_MODEL_ID = 300
_MAX_TIMEOUT = 5.0
_MIN_TIMEOUT = 0.05
_SAFE_ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SAFE_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+@-]*\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:  # noqa: D102
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirect(),
)


@dataclass(frozen=True)
class KnownRuntime:
    name: str
    label: str
    base_url: str


KNOWN_RUNTIMES = (
    KnownRuntime("lm_studio", "LM Studio", "http://127.0.0.1:1234/v1"),
    KnownRuntime("ollama", "Ollama", "http://127.0.0.1:11434/v1"),
    KnownRuntime("llama_cpp", "llama.cpp", "http://127.0.0.1:8080/v1"),
)
_KNOWN_BY_NAME = {runtime.name: runtime for runtime in KNOWN_RUNTIMES}


@dataclass(frozen=True)
class LocalRuntime:
    provider_id: str
    label: str
    base_url: str
    models: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        payload = asdict(self)
        payload["models"] = list(self.models)
        return payload


def canonical_loopback_base_url(raw: str) -> str:
    """Return a canonical OpenAI base URL for a literal loopback endpoint.

    Hostnames are intentionally rejected, including ``localhost``.  Accepting
    only canonical literal IPs removes DNS/rebinding ambiguity and alternate
    numeric spellings which different resolvers interpret inconsistently.
    """

    if not isinstance(raw, str) or raw != raw.strip() or _CONTROL.search(raw):
        raise ValueError("local runtime requires a canonical literal loopback URL")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise ValueError("local runtime requires a canonical literal loopback URL") from exc
    if (
        parts.scheme != "http"
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or port is None
        or not 1 <= port <= 65535
    ):
        raise ValueError("local runtime requires an http literal loopback URL with a port")
    host = parts.hostname
    if not host or "%" in host:
        raise ValueError("local runtime requires a canonical literal loopback host")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("local runtime requires a canonical literal loopback host") from exc
    if getattr(ip, "ipv4_mapped", None) is not None or not ip.is_loopback:
        raise ValueError("local runtime requires a canonical literal loopback host")
    if ip.version == 4:
        if str(ip) != host:
            raise ValueError("local runtime requires a canonical literal loopback host")
        authority = f"{ip}:{port}"
    else:
        if str(ip) != host.lower():
            raise ValueError("local runtime requires a canonical literal loopback host")
        authority = f"[{ip}]:{port}"
    path = parts.path.rstrip("/")
    if path not in {"", "/v1"}:
        raise ValueError("local runtime base URL path must be /v1")
    return f"http://{authority}/v1"


def _runtime_identity(name: str, base_url: str | None) -> tuple[str, str, str]:
    normalized = (name or "").strip().lower().replace("-", "_").replace(".", "_")
    known = _KNOWN_BY_NAME.get(normalized)
    if known is not None:
        return f"local_{known.name}", known.label, canonical_loopback_base_url(
            base_url or known.base_url
        )
    if not _SAFE_ID.fullmatch(normalized):
        raise ValueError("local runtime name must use lowercase letters, digits, and underscores")
    if base_url is None:
        raise ValueError("an unknown local runtime requires --base-url")
    return f"local_{normalized}", name.strip(), canonical_loopback_base_url(base_url)


def _safe_model_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= _MAX_MODEL_ID
        and _SAFE_MODEL_ID.fullmatch(value) is not None
    )


def discover_runtime(
    *,
    name: str,
    base_url: str | None = None,
    timeout: float = 1.0,
) -> LocalRuntime:
    """Perform one bounded, credential-free ``GET /models`` on loopback."""

    provider_id, label, endpoint = _runtime_identity(name, base_url)
    timeout = max(_MIN_TIMEOUT, min(_MAX_TIMEOUT, float(timeout)))
    request = urllib.request.Request(
        f"{endpoint}/models",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with _NO_REDIRECT_OPENER.open(request, timeout=timeout) as response:
            server = str(response.headers.get("Server", ""))
            if "freellmpool" in server.lower():
                raise ValueError("endpoint is a freellmpool proxy, not a local model runtime")
            raw = response.read(_MAX_BODY_BYTES + 1)
    except ValueError:
        raise
    except (
        OSError,
        http.client.HTTPException,
        urllib.error.URLError,
        urllib.error.HTTPError,
    ) as exc:
        raise ValueError(f"local runtime discovery failed: {type(exc).__name__}") from None
    if len(raw) > _MAX_BODY_BYTES:
        raise ValueError(f"local runtime model response exceeds {_MAX_BODY_BYTES} bytes")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("local runtime returned invalid JSON") from exc
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("local runtime /models response requires a data array")
    if len(rows) > _MAX_MODELS:
        raise ValueError(f"local runtime returned more than {_MAX_MODELS} models")
    models: list[str] = []
    for row in rows:
        model = row.get("id") if isinstance(row, dict) else None
        if not _safe_model_id(model):
            raise ValueError("local runtime returned an unsafe model id")
        assert isinstance(model, str)
        if model not in models:
            models.append(model)
    return LocalRuntime(provider_id, label, endpoint, tuple(models))


def discover_known_runtimes(
    *, timeout: float = 1.0
) -> tuple[list[LocalRuntime], list[dict[str, object]]]:
    """Probe the fixed three-runtime list sequentially and return sanitized results."""

    found: list[LocalRuntime] = []
    unavailable: list[dict[str, object]] = []
    for runtime in KNOWN_RUNTIMES:
        try:
            found.append(
                discover_runtime(name=runtime.name, base_url=runtime.base_url, timeout=timeout)
            )
        except ValueError as exc:
            unavailable.append(
                {
                    "name": runtime.name,
                    "base_url": runtime.base_url,
                    "status": "unavailable",
                    "reason": str(exc),
                }
            )
    return found, unavailable


def default_local_catalog_path() -> Path:
    override = os.environ.get("FREELLMPOOL_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "freellmpool" / "providers.toml"


def _markers(provider_id: str) -> tuple[str, str]:
    return (
        f"# freellmpool-local-runtime-begin: {provider_id}",
        f"# freellmpool-local-runtime-end: {provider_id}",
    )


def _managed_pattern(provider_id: str) -> re.Pattern[str]:
    begin, end = _markers(provider_id)
    return re.compile(rf"(?ms)^{re.escape(begin)}\n.*?^{re.escape(end)}\n?")


def _render(runtime: LocalRuntime) -> str:
    if not _SAFE_ID.fullmatch(runtime.provider_id):
        raise ValueError("unsafe local provider id")
    endpoint = canonical_loopback_base_url(runtime.base_url)
    if not runtime.models or len(runtime.models) > _MAX_MODELS:
        raise ValueError("local runtime import requires 1..256 models")
    begin, end = _markers(runtime.provider_id)
    lines = [
        begin,
        "[[provider]]",
        f'id = "{toml_escape(runtime.provider_id)}"',
        f'label = "{toml_escape(runtime.label)}"',
        'adapter = "openai"',
        f'base_url = "{toml_escape(endpoint)}"',
        'auth = "none"',
        "local = true",
        "models = [",
    ]
    seen: set[str] = set()
    for model in runtime.models:
        if not _safe_model_id(model):
            raise ValueError("unsafe local runtime model id")
        assert isinstance(model, str)
        if model in seen:
            continue
        seen.add(model)
        lines.append(f'    {{ name = "{toml_escape(model)}", auto = false }},')
    lines.extend(("]", end, ""))
    return "\n".join(lines)


def _assert_safe_catalog_path(path: Path) -> None:
    """Reject symlinks at the catalog and every existing parent component."""

    absolute_parent = Path(os.path.abspath(os.fspath(path.parent)))
    for component in reversed((absolute_parent, *absolute_parent.parents)):
        try:
            mode = component.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError("local runtime catalog parent directory symlink is not allowed")
        if not stat.S_ISDIR(mode):
            raise ValueError("local runtime catalog parent must be a directory")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode):
        raise ValueError("local runtime catalog symlink is not allowed")


def _lock_file(fd: int) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        import importlib

        msvcrt = importlib.import_module("msvcrt")
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock_file(fd: int) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        import importlib

        msvcrt = importlib.import_module("msvcrt")
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _catalog_lock(path: Path) -> Iterator[None]:
    """Serialize catalog read/modify/write cycles with a private sibling lock.

    On POSIX, also lock the containing directory.  A directory entry can be
    renamed while its old inode remains locked, otherwise allowing a second
    caller to create and lock a replacement sibling.  The stable directory lock
    keeps those callers in one lock domain; the inode check then fails closed if
    the sibling is substituted while this caller is acquiring it.
    """

    _assert_safe_catalog_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _assert_safe_catalog_path(path)
    lock_path = path.with_name(f".{path.name}.lock")
    directory_fd = -1
    directory_locked = False
    try:
        if os.name != "nt":
            directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            directory_flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                directory_fd = os.open(path.parent, directory_flags)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError(
                        "local runtime catalog parent directory changed during lock acquisition"
                    ) from None
                raise
            opened_parent = os.fstat(directory_fd)
            try:
                named_parent = os.stat(path.parent, follow_symlinks=False)
            except FileNotFoundError:
                raise ValueError(
                    "local runtime catalog parent directory changed during lock acquisition"
                ) from None
            if not stat.S_ISDIR(opened_parent.st_mode) or not os.path.samestat(
                opened_parent, named_parent
            ):
                raise ValueError(
                    "local runtime catalog parent directory changed during lock acquisition"
                )
            _lock_file(directory_fd)
            directory_locked = True
            _assert_safe_catalog_path(path)
            try:
                named_parent = os.stat(path.parent, follow_symlinks=False)
            except FileNotFoundError:
                raise ValueError(
                    "local runtime catalog parent directory changed during lock acquisition"
                ) from None
            if not os.path.samestat(os.fstat(directory_fd), named_parent):
                raise ValueError(
                    "local runtime catalog parent directory changed during lock acquisition"
                )

        try:
            lock_mode = lock_path.lstat().st_mode
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(lock_mode):
                raise ValueError("local runtime catalog lock symlink is not allowed")
            if not stat.S_ISREG(lock_mode):
                raise ValueError("local runtime catalog lock must be a regular file")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError("local runtime catalog lock symlink is not allowed") from None
            raise
        lock_locked = False
        try:
            opened_lock = os.fstat(fd)
            if not stat.S_ISREG(opened_lock.st_mode) or opened_lock.st_nlink != 1:
                raise ValueError("local runtime catalog lock must be a private regular file")
            os.fchmod(fd, 0o600)
            _lock_file(fd)
            lock_locked = True
            try:
                named_lock = lock_path.lstat()
            except FileNotFoundError:
                raise ValueError(
                    "local runtime catalog lock changed during lock acquisition"
                ) from None
            opened_lock = os.fstat(fd)
            if (
                not stat.S_ISREG(named_lock.st_mode)
                or opened_lock.st_nlink != 1
                or not os.path.samestat(opened_lock, named_lock)
            ):
                raise ValueError("local runtime catalog lock changed during lock acquisition")
            _assert_safe_catalog_path(path)
            yield
        finally:
            if lock_locked:
                _unlock_file(fd)
            os.close(fd)
    finally:
        if directory_locked:
            _unlock_file(directory_fd)
        if directory_fd >= 0:
            os.close(directory_fd)


def _read_catalog(path: Path) -> str:
    _assert_safe_catalog_path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return ""
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError("local runtime catalog symlink is not allowed") from None
        raise
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            return handle.read()
    except UnicodeError:
        raise ValueError("existing provider catalog is not valid UTF-8") from None
    finally:
        if fd >= 0:
            os.close(fd)


def _provider_ids(text: str) -> tuple[str, ...]:
    try:
        payload = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError):
        raise ValueError("existing provider catalog is invalid TOML; refusing import") from None
    rows = payload.get("provider", [])
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return ()
    return tuple(
        provider_id
        for row in rows
        if isinstance(row, dict)
        and isinstance((provider_id := row.get("id")), str)
    )


def _atomic_write(path: Path, text: str) -> None:
    _assert_safe_catalog_path(path)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_safe_catalog_path(path)
        os.replace(temp_name, path)
        if os.name != "nt":
            directory_fd = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def import_runtime(runtime: LocalRuntime, *, path: Path | None = None) -> Path:
    """Atomically add/update one managed pin-only local runtime provider."""

    path = path or default_local_catalog_path()
    block = _render(runtime)
    pattern = _managed_pattern(runtime.provider_id)
    with _catalog_lock(path):
        existing = _read_catalog(path)
        _provider_ids(existing)  # Validate the complete file before changing it.
        match = pattern.search(existing)
        unmanaged = pattern.sub("", existing, count=1) if match is not None else existing
        if runtime.provider_id in _provider_ids(unmanaged):
            raise ValueError(f"unmanaged provider {runtime.provider_id} already exists")
        if match is not None:
            updated = existing[: match.start()] + block + existing[match.end() :]
        else:
            separator = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
            updated = existing + separator + block
        # Re-assert the private-file contract even for an idempotent re-import.
        _atomic_write(path, updated)
    return path


def remove_runtime(provider_id: str, *, path: Path | None = None) -> bool:
    """Remove only a block previously written by :func:`import_runtime`."""

    if not _SAFE_ID.fullmatch(provider_id):
        raise ValueError("unsafe local provider id")
    path = path or default_local_catalog_path()
    with _catalog_lock(path):
        existing = _read_catalog(path)
        if not existing:
            return False
        _provider_ids(existing)
        updated, count = _managed_pattern(provider_id).subn("", existing, count=1)
        if not count:
            return False
        _atomic_write(path, updated)
        return True
