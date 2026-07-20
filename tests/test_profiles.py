from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from freellmpool.cli import main
from freellmpool.profiles import (
    PROFILES,
    DoctorCheck,
    Profile,
    compatible_profiles,
    profile_with_base_url,
    resolve_profile_for_role,
)


def test_builtin_profiles_cover_supported_agents():
    expected = {
        "metaswarm",
        "opencode",
        "codex",
        "cline",
        "cursor",
        "claude",
        "aider",
        "continue",
        "hermes",
    }
    assert expected.issubset(PROFILES)
    for profile in PROFILES.values():
        assert profile.client_kind in {"openai", "anthropic", "mcp", "shell"}
        assert profile.cost_class in {"free", "metered", "paid"}
        assert profile.base_url
        assert profile.model_family
        assert profile.config_snippets


def test_profile_show_metaswarm_includes_tailnet_and_paid_lane_caveats(capsys):
    assert main(["profile", "show", "metaswarm"]) == 0
    out = capsys.readouterr().out
    assert "Profile: metaswarm" in out
    assert "cost_class:     free" in out
    assert "model_family:   auto" in out
    assert "Tailnet remote agent" in out
    assert "freellmpool tailnet connect" in out
    assert "codex-escalation" in out
    assert "opus-final-review" in out
    assert "user-owned paid" in out


def test_profile_list_surfaces_cost_and_kind(capsys):
    assert main(["profile", "list"]) == 0
    out = capsys.readouterr().out
    assert "name" in out
    assert "kind" in out
    assert "cost" in out
    assert "opencode" in out
    assert "metaswarm" in out


def test_profile_install_prints_quickstart_and_snippets(capsys):
    assert main(["profile", "install", "opencode"]) == 0
    out = capsys.readouterr().out
    assert "freellmpool proxy --port 8080" in out
    assert "opencode.json" in out
    assert '"freellmpool"' in out


def test_hermes_profile_uses_supported_custom_endpoint(capsys):
    profile = PROFILES["hermes"]
    assert profile.client_kind == "openai"
    assert profile.model_family == "quality"
    assert profile.base_url == "http://localhost:8080/v1"
    config = profile.config_snippets["~/.hermes/config.yaml"]
    assert "provider: custom" in config
    assert "default: quality" in config
    assert "base_url: http://localhost:8080/v1" in config
    assert "api_key: anything" in config
    assert "hermes model" in profile.notes

    assert main(["profile", "install", "hermes"]) == 0
    out = capsys.readouterr().out
    assert "~/.hermes/config.yaml" in out
    assert "hermes model" in out


def test_profile_doctor_hermes_dry_run(capsys):
    assert main(["profile", "doctor", "hermes", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "hermes CLI" in out
    assert "freellmpool CLI" in out
    assert "http://localhost:8080/v1/models" in out


def test_unknown_profile_returns_error(capsys):
    assert main(["profile", "show", "bogus"]) == 3
    assert "unknown profile" in capsys.readouterr().err


def _profile(name: str, cost_class: str) -> Profile:
    return Profile(
        name=name,
        label=name,
        client_kind="openai",
        base_url="http://localhost:8080/v1",
        model_family="auto",
        cost_class=cost_class,  # type: ignore[arg-type]
        role_map={"critic": "test role"},
        config_snippets={"shell": "echo test"},
        doctor_checks=(DoctorCheck("url", "models", "http://localhost:8080", "/v1/models"),),
    )


def test_resolver_prefers_safest_cost_class():
    paid = _profile("paid-review", "paid")
    metered = _profile("metered-review", "metered")
    free = _profile("free-review", "free")
    assert compatible_profiles("critic", profiles=(paid, free, metered)) == (
        free,
        metered,
        paid,
    )
    assert resolve_profile_for_role("critic", profiles=(paid, free, metered)) == free


def test_resolver_never_silently_selects_paid_only_profile():
    paid = _profile("paid-review", "paid")
    assert resolve_profile_for_role("critic", profiles=(paid,)) is None
    assert resolve_profile_for_role("critic", explicit_profile="claude") == PROFILES["claude"]


def test_profile_doctor_dry_run_has_no_network_calls(monkeypatch, capsys):
    def fail_network(*_args, **_kwargs):  # pragma: no cover - should never run
        raise AssertionError("dry-run should not call the network")

    monkeypatch.setattr("freellmpool.profiles.urllib.request.urlopen", fail_network)

    assert main(["profile", "doctor", "metaswarm", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "/v1/models" in out
    assert "opencode CLI" in out


class _ModelsHandler(BaseHTTPRequestHandler):
    def log_message(self, _fmt, *_args):
        return

    def do_GET(self):  # noqa: N802
        if self.path == "/v1/models":
            body = json.dumps({"object": "list", "data": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


def test_profile_doctor_opencode_fake_proxy(monkeypatch, capsys):
    monkeypatch.setattr("freellmpool.profiles.shutil.which", lambda name: f"/fake/{name}")

    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert main(["profile", "doctor", "opencode", "--base-url", base_url]) == 0
    finally:
        server.shutdown()
        server.server_close()
    out = capsys.readouterr().out
    assert "doctor results for 'opencode'" in out
    assert "proxy /v1/models" in out
    assert "All required checks passed" in out


def test_profile_doctor_hermes_accepts_authenticated_proxy(monkeypatch, capsys):
    monkeypatch.setattr("freellmpool.profiles.shutil.which", lambda name: f"/fake/{name}")

    class _LockedModelsHandler(_ModelsHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(401)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _LockedModelsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert main(["profile", "doctor", "hermes", "--base-url", base_url]) == 0
    finally:
        server.shutdown()
        server.server_close()
    out = capsys.readouterr().out
    assert "doctor results for 'hermes'" in out
    assert "responded HTTP 401" in out
    assert "All required checks passed" in out


def test_hermes_base_url_override_preserves_v1_contract():
    profile = profile_with_base_url(PROFILES["hermes"], "http://example.test:9000")
    assert profile.base_url == "http://example.test:9000/v1"
    assert [check.url() for check in profile.doctor_checks if check.kind == "url"] == [
        "http://example.test:9000/v1/models"
    ]


def test_profile_with_base_url_normalizes_openai_url():
    profile = profile_with_base_url(PROFILES["opencode"], "http://example.test:9000/v1")
    assert profile.base_url == "http://example.test:9000/v1"
    url_checks = [check for check in profile.doctor_checks if check.kind == "url"]
    assert url_checks[0].url() == "http://example.test:9000/v1/models"
