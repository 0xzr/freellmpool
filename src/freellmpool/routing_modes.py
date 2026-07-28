"""Shared routing-mode normalization.

These helpers keep the library, proxy, and MCP server in sync: a misspelled mode
falls back to the pool default, while public aliases such as ``auto`` mean
"do not override the pool default".
"""

from __future__ import annotations

ROUTING_MODES = (
    "fair",
    "fast",
    "quality",
    "agent",
    "spread",
    "legacy",
    "model",
    "model-fast",
)
PUBLIC_ROUTING_ALIASES = ("auto", "agent", "spread", "fast", "quality", "fair")
_ROUTING_SET = frozenset(ROUTING_MODES)


def normalize_routing_mode(value: str | None, default: str = "fair") -> str:
    """Return a valid internal routing mode, falling back to ``default``."""
    mode = value.strip().lower() if isinstance(value, str) else ""
    if mode in _ROUTING_SET:
        return mode
    fallback = default.strip().lower() if isinstance(default, str) else "fair"
    return fallback if fallback in _ROUTING_SET else "fair"


def routing_override(value: object) -> str | None:
    """Return a valid override mode, or ``None`` for ``auto``/unknown values."""
    if not isinstance(value, str):
        return None
    mode = value.strip().lower()
    if mode == "auto":
        return None
    return mode if mode in _ROUTING_SET else None
