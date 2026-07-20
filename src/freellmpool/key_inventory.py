"""Safe local inventory of manually-created provider keys.

The inventory is metadata only by default: it tracks which provider keys exist,
where they live (env var name), optional dates and notes. Secret values should
stay in env vars, config.toml, a shell profile, or a real secret manager.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .toml_utils import dump_simple_toml, toml_escape

_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bsk-or-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bcsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bnvapi-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9_]{8,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_\-]{8,}\b"),
]


def redact_secrets(text: str) -> str:
    """Return text with common API-key shapes redacted."""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    return redacted


def default_inventory_path() -> Path:
    override = os.environ.get("FREELLMPOOL_KEYS_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "freellmpool" / "keys.toml"


@dataclass(frozen=True)
class KeyRecord:
    provider: str
    env_var: str | None = None
    label: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    commercial_allowed: bool | None = None
    notes: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> KeyRecord:
        return cls(
            provider=str(row.get("provider", "")).strip(),
            env_var=_optional_str(row.get("env_var")),
            label=_optional_str(row.get("label")),
            created_at=_optional_date(row.get("created_at")),
            expires_at=_optional_date(row.get("expires_at")),
            commercial_allowed=_optional_bool(row.get("commercial_allowed")),
            notes=_optional_str(row.get("notes")),
        )

    @property
    def display_label(self) -> str:
        return self.label or self.env_var or self.provider

    def safe_notes(self) -> str | None:
        if self.notes is None:
            return None
        return redact_secrets(self.notes)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _optional_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def load_inventory(path: Path | None = None) -> list[KeyRecord]:
    """Load [[keys]] records from TOML. Missing or invalid files return []."""
    path = path or default_inventory_path()
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return []
    records = []
    for row in data.get("keys", []):
        if not isinstance(row, dict):
            continue
        record = KeyRecord.from_row(row)
        if record.provider:
            records.append(record)
    return records


def records_by_provider(records: list[KeyRecord]) -> dict[str, list[KeyRecord]]:
    grouped: dict[str, list[KeyRecord]] = {}
    for record in records:
        grouped.setdefault(record.provider, []).append(record)
    return grouped


def default_config_path() -> Path:
    """Return the config.toml path used for [keys] values.

    FREELLMPOOL_CONFIG_FILE points to config.toml. FREELLMPOOL_CONFIG is reserved
    for the user provider catalog, matching freellmpool.config.
    """
    override = os.environ.get("FREELLMPOOL_CONFIG_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "freellmpool" / "config.toml"


def _restrict_owner_read_write(fd: int) -> None:
    """Best-effort 0600 permission narrowing for an open secret file descriptor."""
    fchmod = getattr(os, "fchmod", None)
    if not callable(fchmod):
        return
    try:
        fchmod(fd, 0o600)
    except OSError:
        pass


def upsert_config_key(env_var: str, value: str, path: Path | None = None) -> Path:
    """Write one [keys] value to config.toml and return the path.

    This small writer preserves existing top-level tables that we understand,
    but does not preserve comments. It is used by the interactive CLI helper.
    """
    path = path or default_config_path()
    data: dict[str, dict] = {}
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        data = {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        data = {}
    keys = dict(data.get("keys", {}))
    keys[env_var] = value
    data["keys"] = keys
    path.parent.mkdir(parents=True, exist_ok=True)
    # Open at 0o600 *before* writing, so the secret bytes never exist at a
    # world-readable mode (closes the create-then-chmod TOCTOU window). fchmod
    # also narrows an already-existing file, which O_CREAT's mode would not touch.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        fh = os.fdopen(fd, "w", encoding="utf-8")  # takes ownership of fd
    except BaseException:
        os.close(fd)  # fdopen failed — close the raw fd ourselves
        raise
    with fh:  # closes fd on exit
        _restrict_owner_read_write(fd)  # narrow an already-existing file too
        fh.write(dump_simple_toml(data))
    return path


def append_inventory_record(record: KeyRecord, path: Path | None = None) -> Path:
    """Append a metadata record to keys.toml unless the same provider/env exists."""
    path = path or default_inventory_path()
    records = load_inventory(path)
    exists = any(r.provider == record.provider and r.env_var == record.env_var for r in records)
    if not exists:
        records.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_inventory(records), encoding="utf-8")
    return path


def _dump_inventory(records: list[KeyRecord]) -> str:
    chunks = []
    for record in records:
        lines = ["[[keys]]", f'provider = "{toml_escape(record.provider)}"']
        for name in ("env_var", "label", "created_at", "expires_at", "notes"):
            value = getattr(record, name)
            if value:
                lines.append(f'{name} = "{toml_escape(value)}"')
        if record.commercial_allowed is not None:
            lines.append(f"commercial_allowed = {str(record.commercial_allowed).lower()}")
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks) + ("\n" if chunks else "")
