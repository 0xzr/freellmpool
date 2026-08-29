"""Tailnet helper tests: Tailscale detection, bind safety, and token generation.

These tests deliberately do NOT need a real Tailscale install or a real
Tailnet — they monkeypatch ``subprocess.run`` (via the narrow
``runner`` seam in :func:`freellmpool.tailnet.detect_tailnet`) and
fake ``shutil.which`` to simulate the full matrix of states.
"""

from __future__ import annotations

import dataclasses
import subprocess
from types import SimpleNamespace

import pytest

from freellmpool import tailnet
from freellmpool.tailnet import (
    STATE_CLI_MISSING,
    STATE_LOGGED_OUT,
    STATE_MALFORMED,
    STATE_NO_IPV4,
    STATE_USABLE,
    TailnetStatus,
    UnsafeBindError,
    assert_bind_safe,
    detect_tailnet,
    format_setup_hints,
    generate_session_token,
    generate_session_token_simple,
    is_loopback_host,
    is_tailnet_host,
    safe_base_url,
)

# -- Fake runners used to drive detect_tailnet() without a real CLI --------


def _ok_runner(stdout: str):
    """Build a runner that returns a successful CompletedProcess with ``stdout``."""

    def runner(args, timeout):
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="", args=tuple(args))

    return runner


def _nonzero_runner(stderr: str = "tailscale: not logged in\n"):
    """Build a runner that returns a non-zero exit (logged-out style)."""

    def runner(args, timeout):
        return SimpleNamespace(
            returncode=1, stdout="", stderr=stderr, args=tuple(args)
        )

    return runner


def _timeout_runner():
    def runner(args, timeout):
        raise subprocess.TimeoutExpired(cmd="tailscale", timeout=timeout)

    return runner


# -- detect_tailnet: success / failure matrix -----------------------------


def test_detect_returns_usable_with_clean_100_ipv4(monkeypatch):
    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    status = detect_tailnet(runner=_ok_runner("100.64.0.5\n"))
    assert status.state == STATE_USABLE
    assert status.ipv4 == "100.64.0.5"
    assert status.usable is True
    # Raw output is captured (for tests / debugging) but never leaked to the
    # CLI in a way that reveals API keys; we still assert it isn't empty so
    # we know the runner was actually consulted.
    assert status.raw == "100.64.0.5"


def test_detect_picks_first_valid_100_address_out_of_many(monkeypatch):
    """If Tailscale prints several addresses (multi-tailnet), use the first 100.x."""
    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    status = detect_tailnet(
        runner=_ok_runner("fe80::1\n100.127.0.42\n100.64.0.5\n")
    )
    assert status.state == STATE_USABLE
    assert status.ipv4 == "100.127.0.42"


def test_detect_cli_missing(monkeypatch):
    monkeypatch.setattr(tailnet.shutil, "which", lambda _: None)
    status = detect_tailnet(runner=_ok_runner("100.64.0.5\n"))  # should never be called
    assert status.state == STATE_CLI_MISSING
    assert status.ipv4 is None
    assert status.usable is False
    assert "tailscale" in status.detail.lower()


def test_detect_logged_out_via_nonzero(monkeypatch):
    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    status = detect_tailnet(runner=_nonzero_runner("tailscale: not logged in\n"))
    assert status.state == STATE_LOGGED_OUT
    assert status.ipv4 is None
    assert "logged in" in status.detail.lower() or "up" in status.detail.lower()
    # We deliberately do NOT echo stderr verbatim — it can include
    # MagicDNS hostnames and other text the user may not want printed.
    assert "MagicDNS" not in status.detail
    assert "stdout" not in status.detail


def test_detect_logged_out_via_timeout(monkeypatch):
    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    status = detect_tailnet(runner=_timeout_runner())
    assert status.state == STATE_LOGGED_OUT
    assert "timed out" in status.detail.lower() or "responding" in status.detail.lower()


def test_detect_no_ipv4(monkeypatch):
    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    status = detect_tailnet(runner=_ok_runner(""))
    assert status.state == STATE_NO_IPV4
    assert status.ipv4 is None


def test_detect_no_ipv4_whitespace_only(monkeypatch):
    """Defensive: Tailscale might print just a newline — not an error, just empty."""
    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    status = detect_tailnet(runner=_ok_runner("   \n\n"))
    assert status.state == STATE_NO_IPV4


def test_detect_malformed_lan_ip(monkeypatch):
    """192.168.x.x is a LAN address, not a Tailnet one — refuse it."""
    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    status = detect_tailnet(runner=_ok_runner("192.168.1.42\n"))
    assert status.state == STATE_MALFORMED
    assert status.ipv4 is None
    # Detail should mention what we saw so the user can debug.
    assert "192.168.1.42" in status.detail


def test_detect_malformed_garbage(monkeypatch):
    """Random non-IPv4 tokens should be classified as malformed, not silently ignored."""
    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    status = detect_tailnet(runner=_ok_runner("not-an-ip\nalso-not\n"))
    assert status.state == STATE_MALFORMED
    assert status.ipv4 is None


def test_detect_malformed_100_outside_cgnat(monkeypatch):
    """100.0.0.0/8 is split: Tailscale uses 100.64.0.0/10. 100.0.0.0/24 is public."""
    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    status = detect_tailnet(runner=_ok_runner("100.1.2.3\n"))  # public, not Tailnet
    assert status.state == STATE_MALFORMED
    assert status.ipv4 is None


def test_detect_handles_oserror(monkeypatch):
    """If `tailscale` vanishes between the `which` check and exec, treat as missing."""
    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")

    def runner(args, timeout):
        raise OSError(2, "No such file")

    status = detect_tailnet(runner=runner)
    assert status.state == STATE_CLI_MISSING


def test_detect_uses_supplied_binary_path(monkeypatch):
    """When `binary` is provided directly, `which` should NOT be consulted."""
    called = {"which": 0}

    def fake_which(_):
        called["which"] += 1
        return None

    monkeypatch.setattr(tailnet.shutil, "which", fake_which)
    status = detect_tailnet(
        binary="/custom/path/tailscale",
        runner=_ok_runner("100.64.0.99\n"),
    )
    assert status.state == STATE_USABLE
    assert status.ipv4 == "100.64.0.99"
    assert called["which"] == 0


def test_tailnet_status_dataclass_is_frozen():
    s = TailnetStatus(state=STATE_USABLE, ipv4="100.64.0.5")
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        s.state = "tampered"  # type: ignore[misc]


# -- is_loopback_host / is_tailnet_host -----------------------------------


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("100.64.0.5", False),
        ("192.168.1.1", False),
        ("0.0.0.0", False),
        ("", False),
    ],
)
def test_is_loopback_host(host, expected):
    assert is_loopback_host(host) is expected


@pytest.mark.parametrize(
    "host,expected",
    [
        ("100.64.0.1", True),
        ("100.127.255.254", True),
        ("100.63.255.255", False),  # just outside CGNAT
        ("100.128.0.0", False),     # just outside CGNAT
        ("127.0.0.1", False),
        ("192.168.1.1", False),
        ("0.0.0.0", False),
        ("::1", False),
        ("not-an-ip", False),
        ("", False),
    ],
)
def test_is_tailnet_host(host, expected):
    assert is_tailnet_host(host) is expected


# -- assert_bind_safe: the security gate ----------------------------------


def test_bind_safe_allows_loopback_without_auth():
    # No api_key, no flags: loopback must always be allowed.
    assert_bind_safe(host="127.0.0.1", api_key=None)


def test_bind_safe_allows_localhost():
    assert_bind_safe(host="localhost", api_key=None)


def test_bind_safe_allows_ipv6_loopback():
    assert_bind_safe(host="::1", api_key=None)


def test_bind_safe_allows_tailnet_with_auth():
    # The intended Tailnet path: 100.x + auth.
    assert_bind_safe(host="100.64.0.5", api_key="secret")


def test_bind_safe_refuses_tailnet_without_auth():
    with pytest.raises(UnsafeBindError) as ei:
        assert_bind_safe(host="100.64.0.5", api_key=None)
    msg = str(ei.value).lower()
    assert "tailnet" in msg
    assert "100.64.0.5" in str(ei.value)
    assert "--allow-no-auth" in str(ei.value)


def test_bind_safe_refuses_0000_even_with_auth():
    """0.0.0.0 is not a Tailnet address — it must be refused without --allow-lan."""
    with pytest.raises(UnsafeBindError) as ei:
        assert_bind_safe(host="0.0.0.0", api_key="secret")
    assert "--allow-lan" in str(ei.value)


def test_bind_safe_refuses_lan_without_allow_lan():
    with pytest.raises(UnsafeBindError) as ei:
        assert_bind_safe(host="192.168.1.10", api_key="secret")
    assert "--allow-lan" in str(ei.value)


def test_bind_safe_refuses_lan_with_allow_lan_but_no_auth():
    """`--allow-lan` alone is not enough — you also need auth (or --allow-no-auth)."""
    with pytest.raises(UnsafeBindError) as ei:
        assert_bind_safe(host="192.168.1.10", api_key=None, allow_lan=True)
    assert "--allow-no-auth" in str(ei.value) or "proxy key" in str(ei.value).lower()


def test_bind_safe_allows_lan_with_allow_lan_and_auth():
    assert_bind_safe(host="192.168.1.10", api_key="secret", allow_lan=True)


def test_bind_safe_allows_lan_with_allow_lan_and_allow_no_auth():
    assert_bind_safe(
        host="192.168.1.10", api_key=None, allow_lan=True, allow_no_auth=True
    )


def test_bind_safe_allows_tailnet_with_allow_no_auth_explicit():
    """`--allow-no-auth` is the documented escape hatch for Tailnet binds too."""
    assert_bind_safe(
        host="100.64.0.5", api_key=None, allow_no_auth=True
    )


def test_bind_safe_does_not_leak_key_in_error():
    """The error message must mention the host but never the actual api_key value."""
    # Use a non-Tailnet, non-loopback host so the function actually raises
    # (a Tailnet host with a key is the intended safe path and won't raise).
    with pytest.raises(UnsafeBindError) as ei:
        assert_bind_safe(host="192.168.1.10", api_key="sentinel-key-do-not-leak")
    assert "sentinel-key-do-not-leak" not in str(ei.value)


# -- Token generation: entropy + format + non-leakage guarantees ----------


def test_generate_session_token_has_sufficient_entropy():
    a = generate_session_token()
    b = generate_session_token()
    assert a != b
    # token_urlsafe(24) → 32 url-safe chars
    assert len(a) >= 32
    # url-safe alphabet only
    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    assert set(a).issubset(allowed)


def test_generate_session_token_simple_uses_safe_alphabet():
    token = generate_session_token_simple(48)
    assert len(token) == 48
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
    assert set(token).issubset(allowed)


def test_generate_session_token_simple_zero_length():
    assert generate_session_token_simple(0) == ""


# -- format_setup_hints: client-side env vars, no key leakage --------------


def test_format_setup_hints_includes_openai_and_anthropic_urls():
    out = format_setup_hints(base_url="http://100.64.0.5:8080", auth_enabled=False)
    assert "OPENAI_BASE_URL=http://100.64.0.5:8080/v1" in out
    assert "FREELLMPOOL_BASE_URL=http://100.64.0.5:8080/v1" in out
    assert "ANTHROPIC_BASE_URL=http://100.64.0.5:8080" in out
    assert "OPENAI_API_KEY=anything" in out
    assert "dashboard" in out.lower()


def test_format_setup_hints_accepts_only_safe_auth_labels():
    """The include_provider_keys flag exists only as a footgun guard — it must be ignored.

    The function cannot accept a proxy token: callers provide only an actual
    boolean and a closed enum of non-secret labels. ``include_provider_keys``
    is a documented no-op that makes it hard for a future caller to
    accidentally pipe provider API keys through this output path.
    """
    out = format_setup_hints(
        base_url="http://100.64.0.5:8080",
        auth_enabled=True,
        token_label=tailnet.SetupTokenLabel.PROXY_KEY,
        include_provider_keys=True,
    )
    # No proxy-token value can enter this call; only a closed label can.
    assert "sentinel-token" not in out
    assert "<proxy-key>" in out
    # No provider-shaped env (e.g. *_API_KEY) appears unless we wrote it.
    assert "GROQ_API_KEY" not in out
    assert "OPENAI_API_KEY='<proxy-key>'" in out
    assert "ANTHROPIC_API_KEY='<proxy-key>'" in out
    assert "OPENAI_API_KEY=anything" not in out

    with pytest.raises(TypeError):
        format_setup_hints(
            base_url="http://100.64.0.5:8080",
            auth_enabled=True,
            token_label="sentinel-token",  # type: ignore[arg-type]
        )

    with pytest.raises(TypeError):
        format_setup_hints(
            base_url="http://100.64.0.5:8080",
            auth_enabled="sentinel-token",  # type: ignore[arg-type]
        )


class _DisclosureStream:
    def __init__(self, *, tty: bool):
        self.tty = tty
        self.writes: list[str] = []
        self.flushed = False

    def isatty(self) -> bool:
        return self.tty

    def write(self, value: str) -> int:
        self.writes.append(value)
        return len(value)

    def flush(self) -> None:
        self.flushed = True


def test_session_token_disclosure_refuses_non_tty_without_writing():
    stream = _DisclosureStream(tty=False)

    with pytest.raises(RuntimeError, match="interactive terminal"):
        tailnet._disclose_session_token_to_tty("sentinel-session-secret", stream=stream)

    assert stream.writes == []
    assert stream.flushed is False


def test_session_token_disclosure_writes_secret_once_to_tty():
    stream = _DisclosureStream(tty=True)

    tailnet._disclose_session_token_to_tty("sentinel-session-secret", stream=stream)

    output = "".join(stream.writes)
    assert len(stream.writes) == 1
    assert output.count("sentinel-session-secret") == 1
    assert "not saved anywhere" in output
    assert stream.flushed is True


# -- safe_base_url ---------------------------------------------------------


def test_safe_base_url_format():
    assert safe_base_url("100.64.0.5", 8080) == "http://100.64.0.5:8080"
    assert safe_base_url("127.0.0.1", 80) == "http://127.0.0.1:80"
    assert safe_base_url("::1", 8080) == "http://[::1]:8080"


# -- All STATE_* tags exist and are distinct ------------------------------


def test_state_tags_are_distinct_strings():
    tags = {
        STATE_USABLE,
        STATE_CLI_MISSING,
        STATE_LOGGED_OUT,
        STATE_NO_IPV4,
        STATE_MALFORMED,
    }
    assert len(tags) == 5
