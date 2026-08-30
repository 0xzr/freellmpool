from __future__ import annotations

import json

from freellmpool.models import Model, Provider
from freellmpool.tailnet import STATE_CLI_MISSING, STATE_USABLE, TailnetStatus


def _catalog():
    return [
        Provider(
            id="alpha",
            label="Alpha",
            adapter="openai",
            base_url="https://alpha.test/v1",
            key_env="ALPHA_KEY",
            models=(Model("alpha-small"),),
        ),
        Provider(
            id="free",
            label="Keyless",
            adapter="openai",
            base_url="https://free.test/v1",
            auth="none",
            models=(Model("free-small"),),
        ),
    ]


def _patch_init(monkeypatch, *, tailnet=True, which=True):
    from freellmpool import init_wizard

    monkeypatch.setattr(init_wizard, "load_catalog", lambda: _catalog())
    monkeypatch.setattr(
        init_wizard,
        "detect_tailnet",
        lambda: TailnetStatus(
            state=STATE_USABLE if tailnet else STATE_CLI_MISSING,
            ipv4="100.64.0.5" if tailnet else None,
            detail="usable" if tailnet else "`tailscale` was not found on PATH.",
        ),
    )
    monkeypatch.setattr(
        init_wizard.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if which and name in {"opencode", "codex"} else None,
    )


def _isolated_env(monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    providers = tmp_path / "providers.toml"
    monkeypatch.setenv("FREELLMPOOL_CONFIG_FILE", str(config))
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(providers))
    monkeypatch.delenv("ALPHA_KEY", raising=False)
    monkeypatch.delenv("FREELLMPOOL_PROXY_KEY", raising=False)
    return config, providers


def test_init_yes_detect_only_writes_no_files(monkeypatch, tmp_path, capsys):
    from freellmpool.cli import main

    config, providers = _isolated_env(monkeypatch, tmp_path)
    _patch_init(monkeypatch, tailnet=False)

    assert main(["init", "--yes"]) == 0

    out = capsys.readouterr().out
    assert "environment status" in out
    assert "Recommended next commands" in out
    assert "missing key env vars : ALPHA_KEY" in out
    assert "tailscale" in out.lower()
    assert "No files were written." in out
    assert not config.exists()
    assert not providers.exists()


def test_init_json_reports_provider_agent_tailnet_and_proxy_state(monkeypatch, tmp_path, capsys):
    from freellmpool.cli import main

    config, _providers = _isolated_env(monkeypatch, tmp_path)
    config.write_text('[settings]\nproxy_key = "secret"\nport = 9090\n', encoding="utf-8")
    monkeypatch.setenv("ALPHA_KEY", "configured")
    _patch_init(monkeypatch, tailnet=True)

    assert main(["init", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["tailnet"]["state"] == STATE_USABLE
    assert payload["tailnet"]["ipv4"] == "100.64.0.5"
    assert payload["proxy_config"]["config_exists"] is True
    assert payload["proxy_config"]["proxy_key_configured"] is True
    assert payload["proxy_config"]["port"] == 9090
    assert payload["agent_clis"]["opencode"]["installed"] is True
    providers = {provider["id"]: provider for provider in payload["providers"]}
    assert providers["alpha"]["configured"] is True
    assert providers["free"]["keyless"] is True


def test_init_json_counts_config_file_keys_as_configured(monkeypatch, tmp_path, capsys):
    from freellmpool.cli import main

    config, _providers = _isolated_env(monkeypatch, tmp_path)
    config.write_text('[keys]\nALPHA_KEY = "from-file"\n', encoding="utf-8")
    _patch_init(monkeypatch, tailnet=True)

    assert main(["init", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    providers = {provider["id"]: provider for provider in payload["providers"]}
    assert providers["alpha"]["configured"] is True
    assert providers["alpha"]["missing_env"] == []


def test_init_metaswarm_tailnet_plan_includes_copy_pastable_commands(
    monkeypatch, tmp_path, capsys
):
    from freellmpool.cli import main

    _isolated_env(monkeypatch, tmp_path)
    _patch_init(monkeypatch, tailnet=True)

    assert main(["init", "--yes", "--agent", "metaswarm", "--tailnet"]) == 0

    out = capsys.readouterr().out
    assert "Setup terminal (run once)" in out
    assert "Foreground server terminal (keep running)" in out
    assert "Client follow-up terminal (new terminal)" in out
    assert "freellmpool tailnet serve --port 8080" in out
    assert "freellmpool profile install metaswarm" in out
    assert (
        "freellmpool profile doctor metaswarm --dry-run --base-url http://100.64.0.5:8080"
        in out
    )
    assert "freellmpool tailnet connect 100.64.0.5 --port 8080" in out
    assert "OPENAI_BASE_URL=http://100.64.0.5:8080/v1" in out
    setup, foreground, client = _terminal_blocks(out)
    assert "freellmpool profile install metaswarm" in setup
    assert _commands(foreground) == ["freellmpool tailnet serve --port 8080"]
    assert "freellmpool profile doctor metaswarm" in client
    assert "OPENAI_BASE_URL=http://100.64.0.5:8080/v1" in client
    assert "# OpenAI-compatible base URL" in client
    assert "# On the client machine" in client


def test_setup_plans_isolate_foreground_commands_for_local_profiles_tailnet_and_mcp(
    monkeypatch, tmp_path
):
    from freellmpool.init_wizard import detect_environment, render_setup_plan

    _isolated_env(monkeypatch, tmp_path)
    _patch_init(monkeypatch, tailnet=True)
    report = detect_environment()

    cases = (
        (None, False, "freellmpool proxy --port 8080"),
        ("opencode", False, "freellmpool proxy --port 8080"),
        (None, True, "freellmpool tailnet serve --port 8080"),
        ("mcp", False, "freellmpool mcp"),
    )
    for agent, tailnet, expected_foreground in cases:
        out = render_setup_plan(report, agent=agent, tailnet=tailnet)
        setup, foreground, client = _terminal_blocks(out)
        assert _commands(foreground) == [expected_foreground]
        assert expected_foreground not in setup
        assert expected_foreground not in _commands(client)
        if agent == "opencode":
            assert "freellmpool profile install opencode" in setup
            assert "freellmpool profile doctor opencode --dry-run" in client
        if agent == "mcp":
            assert "freellmpool proxy" not in out
            assert "freellmpool tailnet serve" not in out


def test_init_rejects_tailnet_for_stdio_mcp(monkeypatch, tmp_path, capsys):
    from freellmpool.cli import main

    _isolated_env(monkeypatch, tmp_path)
    _patch_init(monkeypatch, tailnet=True)

    assert main(["init", "--yes", "--agent", "mcp", "--tailnet"]) == 3
    assert "local stdio" in capsys.readouterr().err


def _terminal_blocks(output: str) -> tuple[str, str, str]:
    labels = (
        "Setup terminal (run once)",
        "Foreground server terminal (keep running)",
        "Client follow-up terminal (new terminal)",
    )
    blocks = []
    for label in labels:
        section = output.split(label, 1)[1]
        blocks.append(section.split("```bash", 1)[1].split("```", 1)[0])
    return tuple(blocks)


def _commands(block: str) -> list[str]:
    return [
        line.strip()
        for line in block.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_init_tailnet_plan_is_actionable_when_tailscale_missing(monkeypatch, tmp_path, capsys):
    from freellmpool.cli import main

    _isolated_env(monkeypatch, tmp_path)
    _patch_init(monkeypatch, tailnet=False)

    assert main(["init", "--yes", "--agent", "metaswarm", "--tailnet"]) == 0

    out = capsys.readouterr().out
    assert "Tailscale is not usable yet" in out
    assert "`tailscale` was not found on PATH" in out
    assert "freellmpool tailnet serve --port 8080" in out
    assert "OPENAI_BASE_URL=http://<tailnet-host>:8080/v1" in out


def test_init_is_idempotent_and_does_not_clobber_existing_config(
    monkeypatch, tmp_path, capsys
):
    from freellmpool.cli import main

    config, providers = _isolated_env(monkeypatch, tmp_path)
    config.write_text("[settings]\nmode = \"wise\"\n", encoding="utf-8")
    providers.write_text("# user providers stay intact\n", encoding="utf-8")
    _patch_init(monkeypatch, tailnet=True)

    assert main(["init", "--yes", "--agent", "opencode", "--force"]) == 0

    assert config.read_text(encoding="utf-8") == "[settings]\nmode = \"wise\"\n"
    assert providers.read_text(encoding="utf-8") == "# user providers stay intact\n"
    assert "No files were written." in capsys.readouterr().out


def test_init_interactive_choice_can_be_monkeypatched(monkeypatch, tmp_path, capsys):
    from freellmpool.cli import main

    _isolated_env(monkeypatch, tmp_path)
    _patch_init(monkeypatch, tailnet=True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "metaswarm")

    assert main(["init"]) == 0

    out = capsys.readouterr().out
    assert "Interactive setup choices" in out
    assert "target agent : metaswarm" in out


def test_init_interactive_mcp_choice_prints_mcp_command(monkeypatch, tmp_path, capsys):
    from freellmpool.cli import main

    _isolated_env(monkeypatch, tmp_path)
    _patch_init(monkeypatch, tailnet=True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "mcp")

    assert main(["init"]) == 0

    out = capsys.readouterr().out
    assert "target agent : MCP server" in out
    assert "freellmpool mcp" in out


def test_init_unknown_agent_errors(monkeypatch, tmp_path, capsys):
    from freellmpool.cli import main

    _isolated_env(monkeypatch, tmp_path)
    _patch_init(monkeypatch, tailnet=True)

    assert main(["init", "--yes", "--agent", "bogus"]) == 3
    assert "unknown agent" in capsys.readouterr().err
