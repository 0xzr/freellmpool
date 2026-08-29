"""Tailscale Tailnet detection and safe-serving helpers for freellmpool.

The CLI uses these helpers to:

- detect the local Tailnet IPv4 (100.x) via the ``tailscale`` binary
- decide whether a non-loopback bind is safe to use without explicit
  ``--allow-lan`` / ``--allow-no-auth`` overrides
- generate session-only bearer tokens when the user has not configured
  ``FREELLMPOOL_PROXY_KEY`` (or ``[settings].proxy_key``)
- format client setup hints for OpenAI / OpenAI-compatible clients on
  another Tailnet machine

The module deliberately depends only on the standard library. It shells
out to ``tailscale`` (so behaviour is consistent with what the user
sees from the official CLI) and never reads or prints provider API keys.
"""

from __future__ import annotations

import ipaddress
import secrets
import shutil
import string
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TextIO

# Tailscale assigns CGNAT-style 100.64.0.0/10 addresses to nodes. We
# treat any 100.64.0.0/10 address as a valid Tailnet IPv4 and refuse
# everything else for "Tailnet" mode.
_TAILNET_V4_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# How long to wait for the local ``tailscale`` CLI to report an IP.
# Tailscale is a local daemon control client; a hang usually means the
# daemon is wedged or the user is on a machine where tailscaled isn't
# running. Keep this short so a misbehaving CLI can't block the proxy.
_TAILSCALE_TIMEOUT_SECONDS = 4.0


# State tags surfaced by :func:`detect_tailnet` so the CLI can render
# actionable messages without leaking any subprocess internals.
STATE_USABLE = "usable"
STATE_CLI_MISSING = "cli-missing"
STATE_LOGGED_OUT = "logged-out"
STATE_NO_IPV4 = "no-ipv4"
STATE_MALFORMED = "malformed"


class SetupTokenLabel(Enum):
    """Approved non-secret labels for setup and banner output."""

    PROXY_KEY = "<proxy-key>"
    PROXY_KEY_FROM_SERVER = "<proxy-key-from-server>"
    YOUR_PROXY_KEY = "<your-proxy-key>"
    SESSION_REAL_RUN = "<session-token-printed-on-real-run>"
    SESSION_DISCLOSED = "<session-token-shown-above>"


@dataclass(frozen=True)
class TailnetStatus:
    """Outcome of one Tailscale detection attempt.

    Attributes:
        state: one of the ``STATE_*`` tags.
        ipv4: the validated Tailnet IPv4 address (str) when ``state`` is
            ``STATE_USABLE``; ``None`` otherwise.
        raw: the raw stdout from ``tailscale ip -4`` (kept for debugging
            and for tests; CLI output should prefer the structured
            fields and never print this directly).
        detail: a short human-readable hint explaining what is missing
            (used in ``tailnet status`` output and the dry-run banner).
    """

    state: str
    ipv4: str | None = None
    raw: str = ""
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.state == STATE_USABLE and self.ipv4 is not None


def _run_tailscale(
    args: Sequence[str],
    *,
    timeout: float,
    binary: str = "tailscale",
) -> subprocess.CompletedProcess[str]:
    """Run the local ``tailscale`` binary and return a CompletedProcess.

    Split out from :func:`detect_tailnet` so tests can monkeypatch a
    single, narrow seam.
    """
    return subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _validate_tailnet_ipv4(candidate: str) -> str | None:
    """Return ``candidate`` if it's a syntactically valid Tailnet IPv4.

    A valid Tailnet IPv4 parses as an IPv4Address AND falls inside the
    100.64.0.0/10 CGNAT block Tailscale uses. Anything else (loopback,
    LAN, IPv6, garbage) is rejected so the CLI can refuse it.
    """
    if not candidate:
        return None
    try:
        addr = ipaddress.IPv4Address(candidate)
    except (ipaddress.AddressValueError, ValueError):
        return None
    if addr in _TAILNET_V4_NETWORK:
        return str(addr)
    return None


def _cli_missing_status() -> TailnetStatus:
    return TailnetStatus(
        state=STATE_CLI_MISSING,
        detail=(
            "the `tailscale` CLI is not on PATH. "
            "Install Tailscale (https://tailscale.com/download) and log in, "
            "or run `freellmpool proxy` on loopback instead."
        ),
    )


def _logged_out_status() -> TailnetStatus:
    return TailnetStatus(
        state=STATE_LOGGED_OUT,
        detail=(
            "`tailscale` is not logged in. Run `tailscale up` to join your Tailnet, "
            "or run `freellmpool proxy` on loopback instead."
        ),
    )


def _classify_ipv4_output(raw: str) -> TailnetStatus | None:
    """Return a ``STATE_USABLE`` status if ``raw`` contains a Tailnet IPv4.

    Returns ``None`` when the output is empty (so the caller can emit a
    "no-ipv4" status), or a malformed status when there's output but
    no Tailnet-shaped address.
    """
    raw = raw.strip()
    if not raw:
        return None

    seen: list[str] = []
    for line in raw.splitlines():
        token = line.strip()
        if not token:
            continue
        seen.append(token)
        validated = _validate_tailnet_ipv4(token)
        if validated is not None:
            return TailnetStatus(state=STATE_USABLE, ipv4=validated, raw=raw)

    # Output present but no Tailnet IPv4 — could be a LAN address, an
    # IPv6, garbage, or a forked tailscale that uses a different
    # CGNAT range. Never silently bind to a non-Tailnet address.
    return TailnetStatus(
        state=STATE_MALFORMED,
        raw=raw,
        detail=(
            "`tailscale ip -4` output did not contain a 100.64.0.0/10 "
            f"address (saw: {', '.join(seen[:3])}). "
            "Refusing to bind to a non-Tailnet address."
        ),
    )


def detect_tailnet(
    *,
    binary: str | None = "tailscale",
    runner=None,
    timeout: float = _TAILSCALE_TIMEOUT_SECONDS,
) -> TailnetStatus:
    """Detect the local Tailscale Tailnet IPv4 address.

    The function never raises for "expected" failure modes (CLI not
    installed, daemon logged out, daemon has no IPv4, garbage output).
    Each of those maps to a tagged :class:`TailnetStatus` so the caller
    can render a clear, actionable message.

    Args:
        binary: the ``tailscale`` executable to invoke. The default
            (``"tailscale"``) goes through ``shutil.which`` for a PATH
            lookup. Pass an absolute path to skip the PATH check (used
            by some tests and by callers that resolve the binary
            themselves). Pass ``None`` to force the "missing" state
            without invoking the runner.
        runner: callable invoked as ``runner(["ip", "-4"], timeout=...)``
            so tests can monkeypatch the subprocess call.
        timeout: subprocess timeout in seconds. The default is small
            because ``tailscale`` is a local control client.
    """
    if binary is None:
        return _cli_missing_status()
    if binary == "tailscale" and shutil.which(binary) is None:
        return _cli_missing_status()
    if runner is None:

        def runner(args: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
            return _run_tailscale(args, timeout=timeout, binary=binary)

    # Explicit absolute path: trust the caller; only the default name
    # goes through `which`.
    try:
        proc = runner(["ip", "-4"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return TailnetStatus(
            state=STATE_LOGGED_OUT,
            detail=(
                "`tailscale ip -4` timed out — the local Tailscale daemon is not "
                "responding. Run `tailscale status` to diagnose."
            ),
        )
    except OSError as exc:
        # The binary vanished between the `which` check and exec, or
        # we don't have permission to run it. Treat as CLI-missing.
        return TailnetStatus(
            state=STATE_CLI_MISSING,
            detail=f"could not invoke `tailscale`: {exc.strerror or exc}",
        )

    if proc.returncode != 0:
        # `tailscale ip` returns non-zero (and prints to stderr) when
        # the daemon isn't logged in / running. We deliberately do not
        # surface stderr verbatim — it can include MagicDNS hostnames
        # and other text the user may not want echoed back.
        return _logged_out_status()

    classified = _classify_ipv4_output(proc.stdout or "")
    if classified is not None:
        return classified

    return TailnetStatus(
        state=STATE_NO_IPV4,
        detail=(
            "`tailscale ip -4` returned no IPv4 address. "
            "This usually means the node has no usable Tailnet IP yet "
            "(check `tailscale status`)."
        ),
    )


def is_loopback_host(host: str) -> bool:
    """True if ``host`` is a loopback bind target (127.0.0.1, ::1, localhost)."""
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ipaddress.AddressValueError, ValueError):
        return False


def is_tailnet_host(host: str) -> bool:
    """True if ``host`` parses as a 100.64.0.0/10 Tailnet IPv4."""
    return _validate_tailnet_ipv4(host) is not None


def generate_session_token(*, nbytes: int = 24) -> str:
    """Generate a URL-safe random token for one proxy session.

    Used as a last-resort Bearer token when the user has not configured
    ``FREELLMPOOL_PROXY_KEY``/``[settings].proxy_key`` but is serving on
    a non-loopback interface (so a missing key would silently expose
    their pool to the network). 24 bytes → 32 url-safe characters,
    which is plenty of entropy for a session-scoped secret and fits the
    OpenAI/Anthropic "any non-empty string" token convention.

    Args:
        nbytes: entropy in bytes. The default of 24 yields a 32-char
            token; tests may pass a smaller value for speed/assertions.
    """
    return secrets.token_urlsafe(nbytes)


class _NonTTYDisclosureError(RuntimeError):
    """A session secret was about to be disclosed somewhere other than a TTY."""


def _disclose_session_token_to_tty(token: str, *, stream: TextIO) -> None:
    """Write one session secret once, and only to an interactive terminal.

    This is the sole intentional secret-output boundary. Setup formatters and
    ordinary banners receive only booleans and :class:`SetupTokenLabel` values.
    Direct ``write`` keeps this exceptional path distinct from generic printing
    and logging APIs that must never receive a proxy secret.
    """
    if not stream.isatty():
        raise _NonTTYDisclosureError(
            "refusing session-token disclosure without an interactive terminal"
        )
    if not token:
        raise ValueError("session token must be non-empty")
    stream.write(
        "\nfreellmpool: generated a one-session proxy key:\n"
        f"  {token}\n"
        "  (use it as the client bearer token; it is not saved anywhere)\n"
    )
    stream.flush()


# Sensible printable alphabet for the ``-``-stripped fallback token
# used by tests / dry-runs that want a token without url-safe
# padding characters. Kept narrow on purpose; production code calls
# :func:`generate_session_token` which uses ``secrets.token_urlsafe``.
_ALPHANUM = string.ascii_letters + string.digits


def generate_session_token_simple(length: int = 32) -> str:
    """Return a short alphanumeric token. Intended for test fixtures and
    the optional "shell-friendly" form some dry-run banners show.

    Uses :func:`secrets.choice` over an unambiguous alphabet so the
    output is safe to copy/paste into a shell without quoting.
    """
    if length <= 0:
        return ""
    return "".join(secrets.choice(_ALPHANUM) for _ in range(length))


def safe_base_url(host: str, port: int) -> str:
    """Return an ``http://host:port`` base URL for client setup hints.

    The function is intentionally simple — Tailnet serving is plaintext
    HTTP; a Tailnet is a private network and TLS termination belongs
    to a reverse proxy / Tailscale HTTPS feature, not to freellmpool.
    """
    url_host = host
    if not (host.startswith("[") and host.endswith("]")):
        try:
            addr = ipaddress.ip_address(host)
        except (ipaddress.AddressValueError, ValueError):
            pass
        else:
            url_host = f"[{addr}]" if addr.version == 6 else str(addr)
    return f"http://{url_host}:{port}"


def format_setup_hints(
    *,
    base_url: str,
    auth_enabled: bool,
    token_label: SetupTokenLabel = SetupTokenLabel.PROXY_KEY,
    include_provider_keys: bool = False,
) -> str:
    """Render the "how to use this proxy from another machine" block.

    The formatter deliberately cannot accept a proxy key. ``auth_enabled``
    carries only state, while ``token_label`` is restricted to a closed enum of
    non-secret placeholders so a future caller cannot route credential data
    into setup text by accident.

    ``include_provider_keys`` is accepted for symmetry with future
    expansion but is intentionally ignored: provider API keys must
    never appear in this output. The parameter exists so callers can't
    accidentally pass one in by adding a future flag.
    """
    # Defensive: if a caller ever does pass keys through, drop them.
    del include_provider_keys
    if not isinstance(auth_enabled, bool):
        raise TypeError("auth_enabled must be a bool")
    if not isinstance(token_label, SetupTokenLabel):
        raise TypeError("token_label must be a SetupTokenLabel")

    openai_base = f"{base_url}/v1"
    anthropic_base = base_url
    if auth_enabled:
        key_value = token_label.value
        openai_key = f"'{key_value}'"
        openai_note = "# proxy bearer token"
        anthropic_key = f"'{key_value}'"
        anthropic_note = "# proxy x-api-key token"
    else:
        openai_key = "anything"
        openai_note = "# ignored when proxy auth is disabled"
        anthropic_key = "anything"
        anthropic_note = "# ignored when proxy auth is disabled"

    auth_lines = []
    if auth_enabled:
        auth_lines.append(
            f"    export FREELLMPOOL_PROXY_KEY='{token_label.value}'  "
            "# optional local name for the proxy token"
        )
    auth_block = ("\n".join(auth_lines) + "\n") if auth_lines else ""
    return (
        f"  OpenAI-compatible base URL : {openai_base}\n"
        f"  Anthropic Messages base URL: {anthropic_base}\n"
        f"  dashboard                  : {base_url}/dashboard\n"
        "\n"
        "  On the client machine (or in the agent's env):\n"
        f"    export OPENAI_BASE_URL={openai_base}\n"
        f"    export OPENAI_API_KEY={openai_key}        {openai_note}\n"
        f"    export FREELLMPOOL_BASE_URL={openai_base}\n"
        f"    export ANTHROPIC_BASE_URL={anthropic_base}\n"
        f"    export ANTHROPIC_API_KEY={anthropic_key}       {anthropic_note}\n"
        f"{auth_block}"
    )


def assert_bind_safe(
    *,
    host: str,
    api_key: str | None,
    allow_lan: bool = False,
    allow_no_auth: bool = False,
) -> None:
    """Raise :class:`UnsafeBindError` if a non-loopback bind isn't allowed.

    Rules (matching the WU-001 spec):

    - loopback binds are always allowed (no auth required)
    - 100.64.0.0/10 Tailnet IPv4 binds require auth unless
      ``allow_no_auth`` is set (Tailnet = "trusted LAN" but still authed
      by default — that is the whole point of Tailnet serving)
    - any other non-loopback bind requires *both* ``allow_lan`` and a
      configured ``api_key``; an unauthenticated non-loopback bind is
      only allowed when ``allow_no_auth`` is also passed (escape hatch)

    The function never logs the key itself; error messages mention only
    whether a key is set.
    """
    if is_loopback_host(host):
        return

    tailnet = is_tailnet_host(host)
    if tailnet and api_key:
        return  # Tailnet + auth: the intended safe path

    if tailnet and not api_key:
        if allow_no_auth:
            return
        raise UnsafeBindError(
            f"refusing to bind to Tailnet address {host} without a proxy key. "
            "Pass --api-key, set FREELLMPOOL_PROXY_KEY, or pass --allow-no-auth "
            "to acknowledge the risk."
        )

    # Non-loopback, non-Tailnet bind: that's a LAN/internet bind.
    if not allow_lan:
        raise UnsafeBindError(
            f"refusing to bind to {host}: not loopback and not a Tailnet (100.x) "
            "address. Pass --allow-lan to expose the proxy on your LAN, "
            "or use a 100.x Tailnet IPv4 for safer cross-machine access."
        )
    if not api_key and not allow_no_auth:
        raise UnsafeBindError(
            f"refusing to bind to {host} without a proxy key. "
            "Pass --api-key, set FREELLMPOOL_PROXY_KEY, or pass --allow-no-auth "
            "to acknowledge the risk."
        )


class UnsafeBindError(ValueError):
    """Raised by :func:`assert_bind_safe` for disallowed non-loopback binds."""


__all__ = [
    "STATE_USABLE",
    "STATE_CLI_MISSING",
    "STATE_LOGGED_OUT",
    "STATE_NO_IPV4",
    "STATE_MALFORMED",
    "SetupTokenLabel",
    "TailnetStatus",
    "detect_tailnet",
    "is_loopback_host",
    "is_tailnet_host",
    "generate_session_token",
    "generate_session_token_simple",
    "safe_base_url",
    "format_setup_hints",
    "assert_bind_safe",
    "UnsafeBindError",
]
