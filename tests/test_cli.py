"""CLI helpers that don't need network."""

from __future__ import annotations

import json
from types import SimpleNamespace

from freellmpool.cli import _strip_fences
from freellmpool.models import Model, Provider, Reply


def test_strip_plain_json():
    assert _strip_fences('{"a": 1}') == '{"a": 1}'


def test_cli_models_json_is_machine_readable(monkeypatch, capsys, tmp_path):
    from freellmpool.cli import main
    from freellmpool.conformance import FEATURE_TOOLS, STATUS_PASS, ConformanceStore

    catalog = [
        Provider(
            id="ready",
            label="Ready",
            adapter="openai",
            base_url="https://ready.test/v1",
            auth="none",
            models=(Model("on"), Model("off", enabled=False)),
        ),
        Provider(
            id="missing",
            label="Missing",
            adapter="openai",
            base_url="https://missing.test/v1",
            key_env="MISSING_KEY",
            models=(Model("other"),),
        ),
    ]
    monkeypatch.setattr("freellmpool.cli.load_catalog", lambda: catalog)
    monkeypatch.setattr(
        "freellmpool.cli.configured_providers",
        lambda providers: [provider for provider in providers if provider.id == "ready"],
    )
    store = ConformanceStore(tmp_path / "conformance.json")
    store.record(
        catalog[0],
        "on",
        FEATURE_TOOLS,
        status=STATUS_PASS,
        classification="verified",
    )
    monkeypatch.setattr("freellmpool.cli.ConformanceStore", lambda: store)

    assert main(["models", "--json", "--all"]) == 0

    assert json.loads(capsys.readouterr().out) == [
        {
            "provider": "ready",
            "model": "on",
            "enabled": True,
            "configured": True,
            "capabilities": {
                "tools": {
                    "status": "pass",
                    "classification": "verified",
                    "verified_at": json.loads(store.path.read_text())["updated_at"],
                    "verification_count": 1,
                }
            },
            "verified_features": ["tools"],
        },
        {
            "provider": "ready",
            "model": "off",
            "enabled": False,
            "configured": True,
            "capabilities": {},
            "verified_features": [],
        },
        {
            "provider": "missing",
            "model": "other",
            "enabled": True,
            "configured": False,
            "capabilities": {},
            "verified_features": [],
        },
    ]


def test_cli_models_and_conformance_include_plugin_providers(
    monkeypatch, capsys, tmp_path
):
    from freellmpool import plugins
    from freellmpool.cli import main
    from freellmpool.conformance import ConformanceStore

    builtin = Provider(
        id="builtin",
        label="Built in",
        adapter="openai",
        base_url="https://builtin.test/v1",
        auth="none",
        models=(Model("builtin-model"),),
    )
    plugin = Provider(
        id="plugin",
        label="Plugin",
        adapter="plugin-adapter",
        base_url="https://plugin.test/v1",
        auth="none",
        models=(Model("plugin-model"),),
    )
    store = ConformanceStore(tmp_path / "conformance.json")
    captured = []

    def fake_run(provider, model, **kwargs):
        captured.append((provider.id, provider.adapter, model))
        return {"chat": {"status": "pass", "classification": "verified"}}

    monkeypatch.setattr("freellmpool.cli.load_catalog", lambda: [builtin])
    monkeypatch.setattr(plugins, "registered_providers", lambda: [plugin])
    monkeypatch.setattr(
        "freellmpool.cli.configured_providers",
        lambda providers, env=None: providers,
    )
    monkeypatch.setattr("freellmpool.cli.ConformanceStore", lambda: store)
    monkeypatch.setattr("freellmpool.cli.run_target_canaries", fake_run)

    assert main(["models", "--providers", "plugin", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {
            "provider": "plugin",
            "model": "plugin-model",
            "enabled": True,
            "configured": True,
            "capabilities": {},
            "verified_features": [],
        }
    ]

    assert (
        main(
            [
                "conformance",
                "run",
                "--providers",
                "plugin",
                "--features",
                "chat",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert captured == [("plugin", "plugin-adapter", "plugin-model")]


def test_plugin_provider_overrides_builtin_id_in_cli_catalog(monkeypatch, capsys):
    from freellmpool import plugins
    from freellmpool.cli import main

    builtin = Provider(
        id="same",
        label="Built in",
        adapter="openai",
        base_url="https://builtin.test/v1",
        auth="none",
        models=(Model("old"),),
    )
    plugin = Provider(
        id="same",
        label="Plugin override",
        adapter="openai",
        base_url="https://plugin.test/v1",
        auth="none",
        models=(Model("new"),),
    )
    monkeypatch.setattr("freellmpool.cli.load_catalog", lambda: [builtin])
    monkeypatch.setattr(plugins, "registered_providers", lambda: [plugin])
    monkeypatch.setattr(
        "freellmpool.cli.configured_providers",
        lambda providers, env=None: providers,
    )

    assert main(["models", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(row["provider"], row["model"]) for row in rows] == [("same", "new")]


def test_cli_conformance_run_is_bounded_machine_readable(monkeypatch, capsys, tmp_path):
    from freellmpool.cli import main
    from freellmpool.conformance import ConformanceStore

    provider = Provider(
        id="ready",
        label="Ready",
        adapter="openai",
        base_url="https://ready.test/v1",
        auth="none",
        models=(Model("first"), Model("second")),
    )
    store = ConformanceStore(tmp_path / "conformance.json")
    captured = []

    def fake_run(provider, model, **kwargs):
        captured.append((provider.id, model, kwargs["features"], kwargs["timeout"]))
        return {
            "chat": {"status": "pass", "classification": "verified"},
            "tools": {"status": "unsupported", "classification": "unsupported"},
        }

    monkeypatch.setattr("freellmpool.cli.load_catalog", lambda: [provider])
    monkeypatch.setattr("freellmpool.cli.configured_providers", lambda catalog, env=None: catalog)
    monkeypatch.setattr("freellmpool.cli.ConformanceStore", lambda: store)
    monkeypatch.setattr("freellmpool.cli.run_target_canaries", fake_run)

    assert (
        main(
            [
                "conformance",
                "run",
                "--providers",
                "ready",
                "--features",
                "chat,tools",
                "--max-targets",
                "1",
                "--timeout",
                "7",
                "--json",
            ]
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert captured == [("ready", "first", ("chat", "tools"), 7.0)]
    assert output == [
        {
            "provider": "ready",
            "model": "first",
            "features": {
                "chat": {"status": "pass", "classification": "verified"},
                "tools": {"status": "unsupported", "classification": "unsupported"},
            },
        }
    ]
    evidence = store.evidence(provider, "first")
    assert evidence["chat"]["status"] == "pass"
    assert evidence["tools"]["status"] == "unsupported"


def test_cli_conformance_disabled_canary_requires_one_exact_target(
    monkeypatch, capsys, tmp_path
):
    from freellmpool.cli import main
    from freellmpool.conformance import ConformanceStore

    provider = Provider(
        id="candidate",
        label="Candidate",
        adapter="openai",
        base_url="https://candidate.test/v1",
        auth="none",
        models=(Model("enabled"), Model("disabled", enabled=False)),
    )
    store = ConformanceStore(tmp_path / "conformance.json")
    captured = []

    def fake_run(provider, model, **kwargs):
        captured.append((provider.id, model, kwargs["features"]))
        return {"chat": {"status": "pass", "classification": "verified"}}

    monkeypatch.setattr("freellmpool.cli.load_catalog", lambda: [provider])
    monkeypatch.setattr("freellmpool.cli.configured_providers", lambda catalog, env=None: catalog)
    monkeypatch.setattr("freellmpool.cli.ConformanceStore", lambda: store)
    monkeypatch.setattr("freellmpool.cli.run_target_canaries", fake_run)

    assert (
        main(
            [
                "conformance",
                "run",
                "--include-disabled",
                "--provider",
                "candidate",
                "--features",
                "chat",
            ]
        )
        == 2
    )
    assert captured == []
    assert "requires one exact --provider and --model" in capsys.readouterr().err

    assert (
        main(
            [
                "conformance",
                "run",
                "--include-disabled",
                "--provider",
                "candidate",
                "--model",
                "disabled",
                "--features",
                "chat",
                "--json",
            ]
        )
        == 0
    )
    assert captured == [("candidate", "disabled", ("chat",))]
    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "provider": "candidate",
            "model": "disabled",
            "features": {"chat": {"status": "pass", "classification": "verified"}},
        }
    ]


def test_cli_conformance_normal_mode_never_selects_disabled_model(monkeypatch, capsys):
    from freellmpool.cli import main

    provider = Provider(
        id="candidate",
        label="Candidate",
        adapter="openai",
        base_url="https://candidate.test/v1",
        auth="none",
        models=(Model("disabled", enabled=False),),
    )
    monkeypatch.setattr("freellmpool.cli.load_catalog", lambda: [provider])
    monkeypatch.setattr("freellmpool.cli.configured_providers", lambda catalog, env=None: catalog)
    monkeypatch.setattr(
        "freellmpool.cli.run_target_canaries",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not probe")),
    )

    assert (
        main(
            [
                "conformance",
                "run",
                "--providers",
                "candidate",
                "--model",
                "disabled",
                "--features",
                "chat",
            ]
        )
        == 3
    )
    assert "no configured provider/model matched" in capsys.readouterr().err


def test_cli_conformance_run_uses_only_catalog_allowlisted_secret_keys(
    monkeypatch, capsys, tmp_path
):
    from freellmpool.cli import main
    from freellmpool.conformance import ConformanceStore

    secret = "provider-secret-value"
    provider = Provider(
        id="ready",
        label="Ready",
        adapter="openai",
        base_url="https://ready.test/v1",
        key_env="READY_KEY",
        models=(Model("first"),),
    )
    store = ConformanceStore(tmp_path / "conformance.json")
    captured = {}

    def fake_run(provider, model, **kwargs):
        captured.update(kwargs["env"])
        return {"chat": {"status": "pass", "classification": "verified"}}

    monkeypatch.delenv("READY_KEY", raising=False)
    monkeypatch.setenv(
        "FREELLMPOOL_CONFORMANCE_KEYS_JSON",
        json.dumps({"READY_KEY": secret, "NOT_IN_CATALOG": "must-not-be-imported"}),
    )
    monkeypatch.setattr("freellmpool.cli.load_catalog", lambda: [provider])
    monkeypatch.setattr("freellmpool.cli.ConformanceStore", lambda: store)
    monkeypatch.setattr("freellmpool.cli.run_target_canaries", fake_run)

    assert main(["conformance", "run", "--features", "chat", "--json"]) == 0
    output = capsys.readouterr()
    assert captured["READY_KEY"] == secret
    assert "NOT_IN_CATALOG" not in captured
    assert "FREELLMPOOL_CONFORMANCE_KEYS_JSON" not in captured
    assert secret not in output.out
    assert secret not in output.err


def test_cli_conformance_status_json_reads_sanitized_store(monkeypatch, capsys, tmp_path):
    from freellmpool.cli import main
    from freellmpool.conformance import FEATURE_CHAT, STATUS_PASS, ConformanceStore

    provider = Provider(
        id="ready",
        label="Ready",
        adapter="openai",
        base_url="https://ready.test/v1",
        auth="none",
        models=(Model("first"),),
    )
    store = ConformanceStore(tmp_path / "conformance.json")
    store.record(
        provider,
        "first",
        FEATURE_CHAT,
        status=STATUS_PASS,
        classification="verified",
    )
    monkeypatch.setattr("freellmpool.cli.ConformanceStore", lambda: store)

    assert main(["conformance", "status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == 1
    assert payload["targets"]["ready/first"]["features"]["chat"]["status"] == "pass"


def test_cli_tokenmax_smoke(providers, env, quota, monkeypatch, capsys):
    """`freellmpool tokenmax` blasts the fake pool and prints every answer."""
    from helpers import make_post

    from freellmpool.cli import main
    from freellmpool.router import Pool

    pool = Pool(providers, quota=quota, env=env, post=make_post({}))  # all return "ok"
    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: pool))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")

    assert main(["tokenmax", "capital of Australia?", "--no-synthesize"]) == 0
    out = capsys.readouterr().out
    assert "TOKENMAX" in out
    assert "###" in out  # at least one model's answer


def test_cli_tokenmax_synthesizes_by_default(providers, env, quota, monkeypatch, capsys):
    from helpers import make_post

    from freellmpool.cli import main
    from freellmpool.router import Pool

    pool = Pool(providers, quota=quota, env=env, post=make_post({}))
    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: pool))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")

    assert main(["tokenmax", "hi", "--max-models", "2"]) == 0
    out = capsys.readouterr().out
    assert "SYNTHESIS" in out  # the verdict is produced unless --no-synthesize


def test_cli_ask_passes_timeout(monkeypatch, capsys):
    from freellmpool.cli import main
    from freellmpool.router import Pool

    captured = {}

    class FakePool:
        def ask(self, prompt, **kwargs):
            captured["prompt"] = prompt
            captured["timeout"] = kwargs["timeout"]
            return Reply(text="ok", provider_id="fake", model="fake-model", raw={})

    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: FakePool()))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")

    assert main(["ask", "hello", "--timeout", "12.5"]) == 0

    assert captured == {"prompt": "hello", "timeout": 12.5}
    assert capsys.readouterr().out.strip() == "ok"


def test_cli_roles_lists_role_presets(capsys):
    from freellmpool.cli import main

    assert main(["roles"]) == 0

    out = capsys.readouterr().out
    assert "Available roles:" in out
    assert "coder" in out
    assert "critic" in out
    assert "second-opinion" in out


def test_cli_ask_role_applies_role_defaults(monkeypatch, capsys):
    from freellmpool.cli import main
    from freellmpool.router import Pool

    captured = {}

    class FakePool:
        def ask(self, prompt, **kwargs):
            captured.update(kwargs)
            return Reply(text="ok", provider_id="fake", model="fake-model", raw={})

    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: FakePool()))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")

    assert main(["ask", "hello", "--role", "coder"]) == 0

    assert captured["routing"] == "quality"
    assert captured["max_tokens"] == 2048
    assert "programmer" in captured["system"].lower()
    assert capsys.readouterr().out.strip() == "ok"


def test_cli_explicit_task_beats_role_task_default(monkeypatch, capsys):
    from freellmpool.cli import main
    from freellmpool.router import Pool

    captured = {}

    class FakePool:
        def ask(self, prompt, **kwargs):
            captured.update(kwargs)
            return Reply(text="ok", provider_id="fake", model="fake-model", raw={})

    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: FakePool()))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")

    assert (
        main(
            [
                "ask",
                "Read this Markdown",
                "--role",
                "grounded-reader",
                "--task",
                "general",
            ]
        )
        == 0
    )

    assert captured["task"] == "general"
    assert capsys.readouterr().out.strip() == "ok"


def test_cli_grounded_reader_role_passes_its_task_default(monkeypatch, capsys):
    from freellmpool.cli import main
    from freellmpool.router import Pool

    captured = {}

    class FakePool:
        def ask(self, prompt, **kwargs):
            captured.update(kwargs)
            return Reply(text="ok", provider_id="fake", model="fake-model", raw={})

    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: FakePool()))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")

    assert main(["ask", "Read this", "--role", "grounded-reader"]) == 0
    assert captured["task"] == "grounded-reading"
    assert capsys.readouterr().out.strip() == "ok"


def test_cli_ask_second_opinion_prints_two_answers(providers, env, quota, monkeypatch, capsys):
    from helpers import make_post

    from freellmpool.cli import main
    from freellmpool.router import Pool

    pool = Pool(providers, quota=quota, env=env, post=make_post({}))
    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: pool))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")

    assert main(["ask", "compare these options", "--second-opinion", "--opinions", "2"]) == 0

    out = capsys.readouterr().out
    assert "second opinion panel" in out
    assert out.count("###") >= 2


def test_cli_ask_second_opinion_can_synthesize(providers, env, quota, monkeypatch, capsys):
    from helpers import make_post

    from freellmpool.cli import main
    from freellmpool.router import Pool

    pool = Pool(providers, quota=quota, env=env, post=make_post({}))
    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: pool))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")

    assert main(["ask", "compare these options", "--second-opinion", "--opinions", "2", "--synthesize"]) == 0

    out = capsys.readouterr().out
    assert "### synthesis" in out
    assert out.count("###") >= 3


def test_cli_second_opinion_role_uses_panel(providers, env, quota, monkeypatch, capsys):
    from helpers import make_post

    from freellmpool.cli import main
    from freellmpool.router import Pool

    pool = Pool(providers, quota=quota, env=env, post=make_post({}))
    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: pool))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")

    assert main(["ask", "compare these options", "--role", "second-opinion", "--opinions", "2"]) == 0

    out = capsys.readouterr().out
    assert "second opinion panel" in out
    assert out.count("###") >= 2


def test_cli_ask_routing_override_beats_role(monkeypatch):
    from freellmpool.cli import main
    from freellmpool.router import Pool

    captured = {}

    class FakePool:
        def ask(self, prompt, **kwargs):
            captured.update(kwargs)
            return Reply(text="ok", provider_id="fake", model="fake-model", raw={})

    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: FakePool()))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")

    assert main(["ask", "hello", "--role", "coder", "--routing", "fast"]) == 0

    assert captured["routing"] == "fast"


def test_cli_ask_routing_auto_beats_role_with_pool_default(monkeypatch):
    from freellmpool.cli import main
    from freellmpool.router import Pool

    captured = {}

    class FakePool:
        def ask(self, prompt, **kwargs):
            captured.update(kwargs)
            return Reply(text="ok", provider_id="fake", model="fake-model", raw={})

    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: FakePool()))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")

    assert main(["ask", "hello", "--role", "coder", "--routing", "auto"]) == 0

    assert captured["routing"] is None


def test_cli_ask_without_routing_keeps_pool_default(monkeypatch):
    from freellmpool.cli import main
    from freellmpool.router import Pool

    captured = {}

    class FakePool:
        def ask(self, prompt, **kwargs):
            captured.update(kwargs)
            return Reply(text="ok", provider_id="fake", model="fake-model", raw={})

    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: FakePool()))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")

    assert main(["ask", "hello"]) == 0

    assert captured["routing"] is None
    assert captured["max_tokens"] == 1024
    assert captured["temperature"] == 0.0


def test_cli_ask_unknown_role_lists_valid_roles(monkeypatch, capsys):
    from freellmpool.cli import main

    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")

    assert main(["ask", "hello", "--role", "missing-role"]) == 2

    err = capsys.readouterr().err
    assert "unknown role 'missing-role'" in err
    assert "Available roles:" in err
    assert "coder" in err


def test_cli_ask_role_with_explicit_model_keeps_verbose_provenance(monkeypatch, capsys):
    from freellmpool.cli import main
    from freellmpool.router import Pool

    captured = {}

    class FakePool:
        def ask(self, prompt, **kwargs):
            captured.update(kwargs)
            return Reply(text="ok", provider_id="alpha", model="alpha-small", raw={})

    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: FakePool()))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")
    monkeypatch.setattr(
        "freellmpool.cli.configured_providers",
        lambda: [SimpleNamespace(id="alpha")],
    )

    assert main(["ask", "hello", "--role", "coder", "--model", "alpha/alpha-small", "-v"]) == 0

    assert captured["providers"] == ["alpha"]
    assert captured["model"] == "alpha-small"
    err = capsys.readouterr().err
    assert "served by alpha/alpha-small" in err


def test_cli_tokenmax_passes_timeout(monkeypatch):
    from freellmpool.cli import main
    from freellmpool.router import Pool

    captured = {}
    fake_pool = SimpleNamespace(providers=[object()])

    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: fake_pool))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")
    monkeypatch.setattr(
        "freellmpool.tokenmax.select_targets",
        lambda pool, messages, max_models: ([SimpleNamespace()], 1),
    )

    def fake_fan_out(pool, messages, picks, *, max_tokens, timeout, progress=None):
        captured["timeout"] = timeout
        captured["max_tokens"] = max_tokens
        return [("fake/model", "ok")], []

    monkeypatch.setattr("freellmpool.tokenmax.fan_out", fake_fan_out)

    assert main(["tokenmax", "hello", "--timeout", "7.25", "--no-synthesize"]) == 0

    assert captured == {"timeout": 7.25, "max_tokens": 400}


def test_cli_tokenmax_passes_timeout_to_synthesis(monkeypatch, capsys):
    from freellmpool.cli import main
    from freellmpool.router import Pool

    captured = {}
    fake_pool = SimpleNamespace(providers=[object()])

    def fake_chat(messages, **kwargs):
        captured["timeout"] = kwargs["timeout"]
        captured["messages"] = messages
        return Reply(text="summary", provider_id="fake", model="synth-model", raw={})

    fake_pool.chat = fake_chat
    monkeypatch.setattr(Pool, "from_default_config", classmethod(lambda cls: fake_pool))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")
    monkeypatch.setattr(
        "freellmpool.tokenmax.select_targets",
        lambda pool, messages, max_models: ([SimpleNamespace()], 1),
    )
    monkeypatch.setattr(
        "freellmpool.tokenmax.fan_out",
        lambda pool, messages, picks, *, max_tokens, timeout, progress=None: (
            [("fake/model", "answer")],
            [],
        ),
    )

    assert main(["tokenmax", "hello", "--timeout", "11.5"]) == 0

    assert captured["timeout"] == 11.5
    assert "answer" in captured["messages"][0]["content"]
    assert "SYNTHESIS" in capsys.readouterr().out


def test_tokenmax_fan_out_passes_timeout_to_pool_chat():
    from freellmpool.tokenmax import fan_out

    captured = {}
    target = SimpleNamespace(provider=SimpleNamespace(id="fake"), model="model-a")

    class FakePool:
        def chat(self, messages, **kwargs):
            captured["messages"] = messages
            captured["timeout"] = kwargs["timeout"]
            return Reply(text="ok", provider_id="fake", model="model-a", raw={})

    answered, failed = fan_out(
        FakePool(),
        [{"role": "user", "content": "hello"}],
        [target],
        max_tokens=50,
        timeout=3.5,
    )

    assert answered == [("fake/model-a", "ok")]
    assert failed == []
    assert captured == {
        "messages": [{"role": "user", "content": "hello"}],
        "timeout": 3.5,
    }


def test_strip_fenced_json():
    assert _strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_bare_fence():
    assert _strip_fences("```\nhello\n```") == "hello"


def test_cli_capacity_status_smoke(monkeypatch, capsys):
    from freellmpool.cli import main

    monkeypatch.setenv("FREELLMPOOL_KEYS_PATH", "/tmp/freellmpool-test-missing-keys.toml")
    assert main(["capacity", "status", "--target", "1", "--no-catalog-sync"]) == 0
    out = capsys.readouterr().out
    assert "LLM capacity:" in out


def test_cli_capacity_status_is_cache_first_and_refresh_is_explicit(
    tmp_path, monkeypatch, capsys
):
    from freellmpool.cli import main

    calls = []
    monkeypatch.setenv("FREELLMPOOL_KEYS_PATH", str(tmp_path / "missing-keys.toml"))
    monkeypatch.setattr("freellmpool.catalog.load_external_catalog", lambda: [])

    def sync_external_catalog(*, timeout):
        calls.append(timeout)
        return tmp_path / "external.json", []

    monkeypatch.setattr("freellmpool.catalog.sync_external_catalog", sync_external_catalog)

    assert main(["capacity", "status", "--target", "1"]) == 0
    assert calls == []
    assert "External catalog cache:" in capsys.readouterr().out

    assert main(["capacity", "status", "--target", "1", "--refresh"]) == 0
    assert calls == [8.0]
    assert "External catalog refreshed:" in capsys.readouterr().out


def _install_capacity_status_edge_fixture(tmp_path, monkeypatch):
    catalog = [
        Provider(
            id="keyless",
            label="Keyless",
            adapter="openai",
            base_url="https://keyless.test/v1",
            auth="none",
            models=(Model("free-model", rpd=25),),
        ),
        Provider(
            id="low",
            label="Low Quota",
            adapter="openai",
            base_url="https://low.test/v1",
            key_env="LOW_KEY",
            models=(Model("low-model", rpd=10),),
        ),
        Provider(
            id="needskey",
            label="Needs Key",
            adapter="openai",
            base_url="https://needs-key.test/v1",
            key_env="NEEDS_KEY",
            models=(Model("paid-model", rpd=50),),
        ),
    ]

    class FakeQuota:
        def snapshot(self):
            return {"low::low-model": 8}

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("FREELLMPOOL_CONFIG_FILE", str(tmp_path / "config.toml"))
    monkeypatch.setenv("FREELLMPOOL_KEYS_PATH", str(tmp_path / "keys.toml"))
    monkeypatch.setenv("FREELLMPOOL_QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setenv(
        "FREELLMPOOL_EXTERNAL_CATALOG_PATH",
        str(tmp_path / "provider_catalog.json"),
    )
    monkeypatch.setattr("freellmpool.cli.load_catalog", lambda: catalog)
    monkeypatch.setattr("freellmpool.catalog.load_external_catalog", lambda: [])
    monkeypatch.setattr("freellmpool.key_inventory.load_inventory", lambda path=None: [])
    monkeypatch.setattr("freellmpool.capacity.QuotaStore", lambda: FakeQuota())
    monkeypatch.setattr(
        "freellmpool.capacity.effective_env",
        lambda env=None: {"LOW_KEY": "set"},
    )


def test_cli_capacity_status_all_reports_quota_edges_without_local_state(
    tmp_path, monkeypatch, capsys
):
    from freellmpool.cli import main

    _install_capacity_status_edge_fixture(tmp_path, monkeypatch)

    assert main(["capacity", "status", "--all", "--target", "3", "--no-catalog-sync"]) == 0

    out = capsys.readouterr().out
    assert "LLM capacity: 1/3 healthy providers" in out
    assert "External catalog cache: 0 providers" in out
    assert "Warning: 1 provider(s) are near quota." in out
    assert "Action recommended: add 2 provider(s)." in out
    assert "healthy     keyless" in out
    assert "low_quota   low" in out
    assert "missing     needskey" in out
    assert "used=0/25" in out
    assert "used=8/10" in out
    assert "key=keyless" in out
    assert "key=LOW_KEY" in out
    assert "key=NEEDS_KEY" in out


def test_cli_doctor_smoke(tmp_path, monkeypatch, capsys):
    from freellmpool.cli import main

    monkeypatch.setenv("FREELLMPOOL_CONFIG_FILE", str(tmp_path / "config.toml"))
    monkeypatch.setenv("FREELLMPOOL_QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setenv("FREELLMPOOL_CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setenv("FREELLMPOOL_EXTERNAL_CATALOG_PATH", str(tmp_path / "external.json"))

    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "freellmpool" in out
    assert "providers:" in out
    assert "catalog: ok" in out


def test_cli_doctor_uses_effective_config_without_disclosing_secrets(
    tmp_path, monkeypatch, capsys
):
    from freellmpool.cli import main

    secret = "doctor-provider-secret"
    config = tmp_path / "config.toml"
    config.write_text(
        f'[keys]\nDOCTOR_API_KEY = "{secret}"\n[settings]\ncache_ttl = 17\n',
        encoding="utf-8",
    )
    catalog = [
        Provider(
            id="doctor",
            label="Doctor",
            adapter="openai",
            base_url="https://doctor.test/v1",
            key_env="DOCTOR_API_KEY",
            models=(Model("doctor-model"),),
        )
    ]
    monkeypatch.setenv("FREELLMPOOL_CONFIG_FILE", str(config))
    monkeypatch.setenv("FREELLMPOOL_QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setenv("FREELLMPOOL_CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setenv("FREELLMPOOL_EXTERNAL_CATALOG_PATH", str(tmp_path / "external.json"))
    monkeypatch.setattr("freellmpool.cli.load_catalog", lambda: catalog)

    assert main(["doctor"]) == 0
    captured = capsys.readouterr()
    assert "providers: 1/1 configured" in captured.out
    assert "ttl=17" in captured.out
    assert secret not in captured.out
    assert secret not in captured.err


def test_cli_doctor_fails_with_sanitized_toml_location(tmp_path, monkeypatch, capsys):
    from freellmpool.cli import main

    secret = "malformed-secret-must-not-appear"
    config = tmp_path / "config.toml"
    config.write_text(f'[keys]\nGROQ_API_KEY = "{secret}\n', encoding="utf-8")
    monkeypatch.setenv("FREELLMPOOL_CONFIG_FILE", str(config))
    monkeypatch.setenv("FREELLMPOOL_QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setenv("FREELLMPOOL_CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setenv("FREELLMPOOL_EXTERNAL_CATALOG_PATH", str(tmp_path / "external.json"))

    assert main(["doctor"]) == 1
    captured = capsys.readouterr()
    assert "config validation: FAIL" in captured.out
    assert "toml_syntax" in captured.out
    assert "line=" in captured.out
    assert "column=" in captured.out
    assert secret not in captured.out
    assert secret not in captured.err


def test_cli_doctor_fails_on_wrong_config_table_type_without_values(
    tmp_path, monkeypatch, capsys
):
    from freellmpool.cli import main

    config = tmp_path / "config.toml"
    config.write_text('keys = "sensitive-wrong-type-value"\n', encoding="utf-8")
    monkeypatch.setenv("FREELLMPOOL_CONFIG_FILE", str(config))
    monkeypatch.setenv("FREELLMPOOL_QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setenv("FREELLMPOOL_CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setenv("FREELLMPOOL_EXTERNAL_CATALOG_PATH", str(tmp_path / "external.json"))

    assert main(["doctor"]) == 1
    captured = capsys.readouterr()
    assert "table_type" in captured.out
    assert "[keys] must be a table" in captured.out
    assert "sensitive-wrong-type-value" not in captured.out
    assert "sensitive-wrong-type-value" not in captured.err


def test_cli_keys_checklist_smoke(monkeypatch, capsys):
    from freellmpool.cli import main

    monkeypatch.setenv("FREELLMPOOL_KEYS_PATH", "/tmp/freellmpool-test-missing-keys.toml")
    assert main(["keys", "checklist", "--target", "1"]) == 0
    out = capsys.readouterr().out
    assert "healthy providers" in out or "Manual key checklist" in out


def test_cli_keys_add_confirms_fuzzy_external_match(tmp_path, monkeypatch, capsys):
    from freellmpool.cli import main

    cache = tmp_path / "provider_catalog.json"
    user_catalog = tmp_path / "providers.toml"
    config = tmp_path / "config.toml"
    inventory = tmp_path / "keys.toml"
    cache.write_text(
        '{"providers":[{"name":"Hyperbolic","baseUrl":"https://api.hyperbolic.xyz/v1",'
        '"models":[{"id":"meta-llama/Llama-3.3-70B-Instruct","modality":"Text","rateLimit":"100 RPD"}]}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("FREELLMPOOL_EXTERNAL_CATALOG_PATH", str(cache))
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(user_catalog))
    monkeypatch.setenv("FREELLMPOOL_CONFIG_FILE", str(config))
    monkeypatch.setenv("FREELLMPOOL_KEYS_PATH", str(inventory))
    answers = iter(["y", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    assert main(["keys", "add", "Hyperbolc", "--value", "secret"]) == 0

    assert 'id = "hyperbolic"' in user_catalog.read_text()
    assert 'HYPERBOLIC_API_KEY = "secret"' in config.read_text()
    assert 'provider = "hyperbolic"' in inventory.read_text()
    assert "Imported external provider 'Hyperbolic'" in capsys.readouterr().out


def test_cli_keys_add_creates_manual_provider(tmp_path, monkeypatch):
    from freellmpool.cli import main

    user_catalog = tmp_path / "providers.toml"
    config = tmp_path / "config.toml"
    inventory = tmp_path / "keys.toml"
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(user_catalog))
    monkeypatch.setenv("FREELLMPOOL_CONFIG_FILE", str(config))
    monkeypatch.setenv("FREELLMPOOL_KEYS_PATH", str(inventory))
    monkeypatch.setattr("freellmpool.cli._load_or_sync_external_catalog", lambda: [])
    answers = iter(["y", "https://api.hyperbolic.xyz/v1", "meta-llama/Llama-3.3-70B-Instruct", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    assert main(["keys", "add", "Hyperbolic", "--value", "secret"]) == 0

    assert 'id = "hyperbolic"' in user_catalog.read_text()
    assert 'name = "meta-llama/Llama-3.3-70B-Instruct"' in user_catalog.read_text()
    assert 'HYPERBOLIC_API_KEY = "secret"' in config.read_text()


def test_cli_keys_add_cloudflare_prompts_for_account_id(tmp_path, monkeypatch, capsys):
    from freellmpool.cli import main
    from freellmpool.config import effective_env, load_catalog

    config = tmp_path / "config.toml"
    inventory = tmp_path / "keys.toml"
    monkeypatch.setenv("FREELLMPOOL_CONFIG_FILE", str(config))
    monkeypatch.setenv("FREELLMPOOL_KEYS_PATH", str(inventory))
    answers = iter(["account-123", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    assert main(["keys", "add", "cloudflare", "--value", "token-secret"]) == 0

    text = config.read_text()
    assert 'CLOUDFLARE_API_TOKEN = "token-secret"' in text
    assert 'CLOUDFLARE_ACCOUNT_ID = "account-123"' in text
    env = effective_env({"FREELLMPOOL_CONFIG_FILE": str(config)})
    cloudflare = next(p for p in load_catalog() if p.id == "cloudflare")
    assert cloudflare.is_configured(env)
    out = capsys.readouterr().out
    assert "CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID" in out
    assert "Unlocked " in out
    assert "freellmpool providers health -p cloudflare" in out
    assert "python3 -m freellmpool" not in out


def test_cli_keys_add_cloudflare_uses_existing_account_id(tmp_path, monkeypatch, capsys):
    from freellmpool.cli import main
    from freellmpool.config import effective_env, load_catalog

    config = tmp_path / "config.toml"
    inventory = tmp_path / "keys.toml"
    config.write_text(
        '[keys]\nCLOUDFLARE_API_TOKEN = "old-token"\nCLOUDFLARE_ACCOUNT_ID = "account-123"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("FREELLMPOOL_CONFIG_FILE", str(config))
    monkeypatch.setenv("FREELLMPOOL_KEYS_PATH", str(inventory))
    prompts = []

    def answer_confirm(prompt=""):
        prompts.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", answer_confirm)

    assert main(["keys", "add", "cloudflare", "--value", "new-token"]) == 0

    text = config.read_text()
    assert 'CLOUDFLARE_API_TOKEN = "new-token"' in text
    assert 'CLOUDFLARE_ACCOUNT_ID = "account-123"' in text
    env = effective_env({"FREELLMPOOL_CONFIG_FILE": str(config)})
    cloudflare = next(p for p in load_catalog() if p.id == "cloudflare")
    assert cloudflare.is_configured(env)
    assert len(prompts) == 1
    assert "CLOUDFLARE_ACCOUNT_ID" not in prompts[0]
    assert "Wrote: CLOUDFLARE_API_TOKEN" in capsys.readouterr().out


def test_cli_keys_add_autodiscovers_model_when_blank(tmp_path, monkeypatch):
    from freellmpool.cli import main

    user_catalog = tmp_path / "providers.toml"
    config = tmp_path / "config.toml"
    inventory = tmp_path / "keys.toml"
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(user_catalog))
    monkeypatch.setenv("FREELLMPOOL_CONFIG_FILE", str(config))
    monkeypatch.setenv("FREELLMPOOL_KEYS_PATH", str(inventory))
    monkeypatch.setattr("freellmpool.cli._load_or_sync_external_catalog", lambda: [])
    monkeypatch.setattr(
        "freellmpool.catalog.discover_openai_models",
        lambda base_url, api_key=None, timeout=10.0: ["model-a", "model-b"],
    )
    answers = iter(["y", "https://api.example.test/v1", "", "2", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    assert main(["keys", "add", "Example", "--value", "secret"]) == 0

    assert 'id = "example"' in user_catalog.read_text()
    assert 'name = "model-b"' in user_catalog.read_text()
    assert 'EXAMPLE_API_KEY = "secret"' in config.read_text()


def test_cli_providers_health_smoke(monkeypatch, capsys):
    from freellmpool.cli import main

    monkeypatch.setattr(
        "freellmpool.cli.cmd_providers_health",
        lambda args: print("health smoke") or 0,
    )
    assert main(["providers", "health"]) == 0
    assert "health smoke" in capsys.readouterr().out


def test_dashboard_contains_capacity(monkeypatch):
    from freellmpool.models import Model, Provider
    from freellmpool.proxy import _dashboard_html
    from freellmpool.router import Pool

    provider = Provider(
        id="demo",
        label="Demo",
        adapter="openai",
        base_url="https://example.test/v1",
        auth="none",
        models=(Model("model"),),
    )
    html = _dashboard_html(Pool([provider]))
    assert "healthy providers" in html
    assert "capacity" in html
    assert "demo" in html


# -- WU-001 tailnet CLI ---------------------------------------------------


class _FakePool:
    """Minimal stand-in for freellmpool.cli.Pool used by the proxy/serve tests.

    The real Pool has many surface methods; these tests only exercise
    the start/stop path (and `quota.flush` + `stats_snapshot` on Ctrl-C).
    Anything that would reach the network is intercepted at the
    `freellmpool.proxy.serve` seam, so this fake can be empty.
    """

    def __init__(self, providers=None):
        # Default to a single fake provider so the "no providers
        # configured" guard in cmd_proxy doesn't fire. Tests that need
        # an empty pool pass `providers=[]` explicitly.
        if providers is None:
            providers = [SimpleNamespace(id="fake", label="Fake", models=[SimpleNamespace()])]
        self.providers = list(providers)
        self.quota = SimpleNamespace(flush=lambda: None)
        self.stats_snapshot = lambda: {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    @classmethod
    def from_default_config(cls):
        return cls()


def _patch_pool(monkeypatch, providers=None):
    """Replace Pool.from_default_config with a controllable fake."""
    monkeypatch.setattr(
        "freellmpool.cli.Pool", SimpleNamespace(from_default_config=lambda: _FakePool(providers))
    )


def _patch_serve(monkeypatch, captured=None):
    """Replace proxy.serve with a fake that records host/port/key and short-circuits."""
    class FakeServer:
        def __init__(self, pool, host="127.0.0.1", port=8080, api_key=None):
            if captured is not None:
                captured["host"] = host
                captured["port"] = port
                captured["api_key"] = api_key
            self.pool = pool

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr("freellmpool.proxy.serve", FakeServer)



def test_cli_tailnet_status_usable(monkeypatch, capsys):
    from freellmpool import tailnet
    from freellmpool.cli import main

    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(
        tailnet,
        "detect_tailnet",
        lambda *, binary=None, runner=tailnet._run_tailscale, timeout=4.0: tailnet.TailnetStatus(
            state=tailnet.STATE_USABLE, ipv4="100.64.0.5", raw="100.64.0.5\n",
        ),
    )

    assert main(["tailnet", "status"]) == 0
    out = capsys.readouterr().err
    assert "usable" in out
    assert "100.64.0.5" in out
    assert "tailnet serve" in out  # next-step hint


def test_cli_tailnet_status_cli_missing(monkeypatch, capsys):
    from freellmpool import tailnet
    from freellmpool.cli import main

    monkeypatch.setattr(tailnet.shutil, "which", lambda _: None)
    assert main(["tailnet", "status"]) == 1
    out = capsys.readouterr().err
    assert "missing" in out.lower()
    assert "127.0.0.1" in out  # fallback hint


def test_cli_tailnet_status_logged_out(monkeypatch, capsys):
    from freellmpool import tailnet
    from freellmpool.cli import main

    monkeypatch.setattr(
        tailnet,
        "detect_tailnet",
        lambda *, binary=None, runner=tailnet._run_tailscale, timeout=4.0: tailnet.TailnetStatus(
            state=tailnet.STATE_LOGGED_OUT,
            detail="`tailscale` is not logged in. Run `tailscale up`.",
        ),
    )

    assert main(["tailnet", "status"]) == 1
    out = capsys.readouterr().err
    assert "logged out" in out.lower()


def test_cli_tailnet_status_malformed(monkeypatch, capsys):
    from freellmpool import tailnet
    from freellmpool.cli import main

    monkeypatch.setattr(
        tailnet,
        "detect_tailnet",
        lambda *, binary=None, runner=tailnet._run_tailscale, timeout=4.0: tailnet.TailnetStatus(
            state=tailnet.STATE_MALFORMED,
            raw="192.168.1.42\n",
            detail="`tailscale ip -4` output did not contain a 100.64.0.0/10 address (saw: 192.168.1.42).",
        ),
    )

    assert main(["tailnet", "status"]) == 1
    out = capsys.readouterr().err
    assert "malformed" in out.lower()
    assert "192.168.1.42" in out  # echoed for debug


def test_cli_tailnet_serve_dry_run_uses_detected_ip(monkeypatch, capsys):
    from freellmpool import tailnet
    from freellmpool.cli import main

    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(
        tailnet,
        "detect_tailnet",
        lambda *, binary=None, runner=tailnet._run_tailscale, timeout=4.0: tailnet.TailnetStatus(
            state=tailnet.STATE_USABLE, ipv4="100.64.0.5", raw="100.64.0.5\n",
        ),
    )

    assert main(["tailnet", "serve", "--dry-run", "--port", "9999"]) == 0
    out = capsys.readouterr().err
    assert "dry run" in out.lower()
    assert "100.64.0.5" in out
    assert "9999" in out
    assert "OPENAI_BASE_URL=http://100.64.0.5:9999/v1" in out
    assert "FREELLMPOOL_BASE_URL=http://100.64.0.5:9999/v1" in out
    # Dry-run must NOT print the real session token.
    assert "<session-token-printed-on-real-run>" in out
    assert "OPENAI_API_KEY='<session-token-printed-on-real-run>'" in out
    assert "OPENAI_API_KEY=anything" not in out


def test_cli_tailnet_serve_refuses_when_tailscale_missing(monkeypatch, capsys):
    from freellmpool import tailnet
    from freellmpool.cli import main

    monkeypatch.setattr(tailnet.shutil, "which", lambda _: None)
    assert main(["tailnet", "serve", "--port", "8080"]) == 3
    err = capsys.readouterr().err
    # The exact wording is part of the user contract: tell the user the
    # CLI is missing and that the loopback proxy still works.
    assert "tailscale" in err.lower()
    assert "loopback" in err.lower() or "127.0.0.1" in err
    # Refuses without leaking any API keys.
    assert "API_KEY" not in err
    assert "Bearer" not in err


def test_cli_tailnet_serve_generates_session_token_when_no_key(monkeypatch, capsys):
    """An interactive run shows its generated key once, outside setup hints."""
    import sys

    from freellmpool import tailnet
    from freellmpool.cli import main

    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(
        tailnet,
        "detect_tailnet",
        lambda *, binary=None, runner=tailnet._run_tailscale, timeout=4.0: tailnet.TailnetStatus(
            state=tailnet.STATE_USABLE, ipv4="100.64.0.5", raw="100.64.0.5\n",
        ),
    )
    captured = {}
    _patch_pool(monkeypatch)
    _patch_serve(monkeypatch, captured=captured)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)

    assert main(["tailnet", "serve", "--port", "1234"]) == 0
    out = capsys.readouterr().err
    assert "session proxy key" in out.lower()
    assert "100.64.0.5" in out
    assert "1234" in out
    assert out.count(captured["api_key"]) == 1
    assert "OPENAI_API_KEY='<session-token-shown-above>'" in out
    assert "OPENAI_API_KEY=anything" not in out
    # No provider API keys appear in the output.
    assert "GROQ_API_KEY" not in out
    assert "ALPHA_KEY" not in out
    assert "BETA_KEY" not in out


def test_cli_tailnet_serve_refuses_generated_key_without_tty(monkeypatch, capsys):
    """Redirected stderr must never receive an auto-generated bearer key."""
    import sys

    from freellmpool import tailnet
    from freellmpool.cli import main

    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(
        tailnet,
        "detect_tailnet",
        lambda *, binary=None, runner=tailnet._run_tailscale, timeout=4.0: tailnet.TailnetStatus(
            state=tailnet.STATE_USABLE, ipv4="100.64.0.5", raw="100.64.0.5\n",
        ),
    )
    monkeypatch.setattr(tailnet, "generate_session_token", lambda: "generated-secret")
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    captured = {}
    _patch_pool(monkeypatch)
    _patch_serve(monkeypatch, captured=captured)

    assert main(["tailnet", "serve", "--port", "1234"]) == 2
    out = capsys.readouterr().err
    assert "non-interactive" in out.lower()
    assert "--api-key" in out
    assert "FREELLMPOOL_PROXY_KEY" in out
    assert "generated-secret" not in out
    assert captured == {}


def test_cli_tailnet_serve_uses_explicit_api_key_without_generating(monkeypatch, capsys):
    """If the user supplies --api-key, no new session token is generated."""
    from freellmpool import tailnet
    from freellmpool.cli import main

    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(
        tailnet,
        "detect_tailnet",
        lambda *, binary=None, runner=tailnet._run_tailscale, timeout=4.0: tailnet.TailnetStatus(
            state=tailnet.STATE_USABLE, ipv4="100.64.0.5", raw="100.64.0.5\n",
        ),
    )
    captured = {}
    _patch_pool(monkeypatch)
    _patch_serve(monkeypatch, captured=captured)

    assert main(["tailnet", "serve", "--port", "1234", "--api-key", "user-supplied-key"]) == 0
    out = capsys.readouterr().err
    # The user-supplied key is forwarded to the server, but not echoed in the banner.
    assert captured["api_key"] == "user-supplied-key"
    assert "session proxy key" not in out.lower()  # no auto-generated token
    # The token *value* itself must not appear in the banner.
    assert "user-supplied-key" not in out
    assert "OPENAI_API_KEY='<your-proxy-key>'" in out


def test_cli_tailnet_secret_never_enters_setup_formatter(monkeypatch, capsys):
    from freellmpool import tailnet
    from freellmpool.cli import main

    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(
        tailnet,
        "detect_tailnet",
        lambda *, binary=None, runner=tailnet._run_tailscale, timeout=4.0: tailnet.TailnetStatus(
            state=tailnet.STATE_USABLE, ipv4="100.64.0.5", raw="100.64.0.5\n",
        ),
    )
    formatter_calls = []
    monkeypatch.setattr(
        tailnet,
        "format_setup_hints",
        lambda **kwargs: formatter_calls.append(kwargs) or "safe setup block\n",
    )
    captured = {}
    _patch_pool(monkeypatch)
    _patch_serve(monkeypatch, captured=captured)

    assert main(["tailnet", "serve", "--api-key", "sentinel-proxy-secret"]) == 0
    assert captured["api_key"] == "sentinel-proxy-secret"
    assert len(formatter_calls) == 1
    assert set(formatter_calls[0]) == {"base_url", "auth_enabled", "token_label"}
    assert formatter_calls[0]["auth_enabled"] is True
    assert formatter_calls[0]["token_label"] is tailnet.SetupTokenLabel.YOUR_PROXY_KEY
    assert "sentinel-proxy-secret" not in repr(formatter_calls[0])
    assert "sentinel-proxy-secret" not in capsys.readouterr().err


def test_cli_tailnet_serve_refuses_allow_lan_without_auth(monkeypatch, capsys):
    """`--allow-lan` on a LAN host still requires auth or --allow-no-auth."""
    from freellmpool import tailnet
    from freellmpool.cli import main

    # Simulate Tailscale reporting a LAN address (rare but possible with
    # subnet routers, exit nodes, or a forked tailscale). The CLI should
    # still refuse without --allow-lan + auth.
    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(
        tailnet,
        "detect_tailnet",
        lambda *, binary=None, runner=tailnet._run_tailscale, timeout=4.0: tailnet.TailnetStatus(
            state=tailnet.STATE_USABLE, ipv4="100.64.0.5", raw="100.64.0.5\n",
        ),
    )
    # Force the bind-safety check to fail by pretending 100.64.0.5 isn't a
    # tailnet host (e.g. a unit test for the LAN branch).
    monkeypatch.setattr(tailnet, "is_tailnet_host", lambda host: False)

    assert main(["tailnet", "serve", "--port", "1234", "--api-key", "k"]) == 2
    out = capsys.readouterr().err
    assert "--allow-lan" in out


def test_cli_tailnet_connect_prints_client_setup(monkeypatch, capsys):
    from freellmpool.cli import main

    assert main(["tailnet", "connect", "laptop.tailnet.local", "--port", "7777"]) == 0
    out = capsys.readouterr().err
    assert "OpenAI-compatible base URL" in out
    assert "http://laptop.tailnet.local:7777/v1" in out
    assert "FREELLMPOOL_BASE_URL=http://laptop.tailnet.local:7777/v1" in out
    assert "OPENAI_BASE_URL=http://laptop.tailnet.local:7777/v1" in out
    assert "ANTHROPIC_BASE_URL=http://laptop.tailnet.local:7777" in out
    # Never leaks any provider API keys.
    assert "GROQ_API_KEY" not in out
    assert "OPENAI_API_KEY='<proxy-key-from-server>'" in out
    assert "ANTHROPIC_API_KEY='<proxy-key-from-server>'" in out
    assert "OPENAI_API_KEY=anything" not in out


def test_cli_proxy_tailnet_alias_delegates_to_tailnet_serve(monkeypatch, capsys):
    """`freellmpool proxy --tailnet` must use the same safety logic as `tailnet serve`."""
    from freellmpool import tailnet
    from freellmpool.cli import main

    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(
        tailnet,
        "detect_tailnet",
        lambda *, binary=None, runner=tailnet._run_tailscale, timeout=4.0: tailnet.TailnetStatus(
            state=tailnet.STATE_USABLE, ipv4="100.64.0.5", raw="100.64.0.5\n",
        ),
    )
    captured = {}
    _patch_pool(monkeypatch)
    _patch_serve(monkeypatch, captured=captured)

    assert main(["proxy", "--tailnet", "--port", "4242", "--api-key", "abc"]) == 0
    out = capsys.readouterr().err
    assert "100.64.0.5" in out
    assert "4242" in out
    # The alias uses the same banner wording as `tailnet serve`.
    assert "Tailnet" in out
    # The alias must bind to the detected Tailnet IP (not 127.0.0.1).
    assert captured["host"] == "100.64.0.5"
    assert captured["api_key"] == "abc"


def test_cli_proxy_tailnet_alias_refuses_missing_tailscale(monkeypatch, capsys):
    """`proxy --tailnet` should not silently fall back to loopback when tailscale is missing."""
    from freellmpool import tailnet
    from freellmpool.cli import main

    monkeypatch.setattr(tailnet.shutil, "which", lambda _: None)
    assert main(["proxy", "--tailnet", "--port", "8080"]) == 3
    out = capsys.readouterr().err
    assert "tailscale" in out.lower()
    assert "127.0.0.1" in out  # the fallback hint to use loopback


def test_cli_proxy_refuses_unsafe_non_loopback_bind(monkeypatch, capsys):
    """`proxy` on a non-loopback, non-Tailnet host without --allow-lan must refuse."""
    from freellmpool.cli import main

    # No providers needed — the safety check fires before pool construction.
    assert main(["proxy", "--host", "192.168.1.10", "--port", "8080"]) == 2
    out = capsys.readouterr().err
    assert "--allow-lan" in out
    assert "192.168.1.10" in out


def test_cli_proxy_refuses_unsafe_bind_before_missing_provider_error(monkeypatch, capsys):
    """Safety errors should be shown even on machines with no configured providers."""
    from freellmpool.cli import main

    _patch_pool(monkeypatch, providers=[])

    assert main(["proxy", "--host", "192.168.1.10", "--port", "8080"]) == 2
    out = capsys.readouterr().err
    assert "--allow-lan" in out
    assert "no providers configured" not in out


def test_cli_proxy_allows_unsafe_bind_with_allow_lan_and_key(monkeypatch, capsys):
    """`proxy --host 192.168.1.10 --allow-lan --api-key K` should reach the server."""
    from freellmpool.cli import main

    captured = {}
    formatter_calls = []
    from freellmpool import tailnet

    monkeypatch.setattr(
        tailnet,
        "format_setup_hints",
        lambda **kwargs: formatter_calls.append(kwargs) or "safe setup block\n",
    )
    _patch_pool(monkeypatch)
    _patch_serve(monkeypatch, captured=captured)

    assert main(
        [
            "proxy",
            "--host",
            "192.168.1.10",
            "--port",
            "8080",
            "--allow-lan",
            "--api-key",
            "sentinel-lan-proxy-secret",
        ]
    ) == 0
    assert captured == {
        "host": "192.168.1.10",
        "port": 8080,
        "api_key": "sentinel-lan-proxy-secret",
    }
    assert len(formatter_calls) == 1
    assert set(formatter_calls[0]) == {"base_url", "auth_enabled", "token_label"}
    assert formatter_calls[0]["auth_enabled"] is True
    assert formatter_calls[0]["token_label"] is tailnet.SetupTokenLabel.YOUR_PROXY_KEY
    assert "sentinel-lan-proxy-secret" not in repr(formatter_calls[0])


def test_cli_proxy_loopback_no_key_unchanged(monkeypatch, capsys):
    """Backward compat: `freellmpool proxy` on loopback with no key still works."""
    from freellmpool import tailnet
    from freellmpool.cli import main

    # The is_loopback_host check must allow 127.0.0.1 even with no key.
    assert tailnet.is_loopback_host("127.0.0.1") is True

    captured = {}
    _patch_pool(monkeypatch)
    _patch_serve(monkeypatch, captured=captured)

    assert main(["proxy"]) == 0  # default host 127.0.0.1, no api key
    out = capsys.readouterr().err
    assert "127.0.0.1" in out
    # No "WARNING" loopback backstop message.
    assert "WARNING" not in out
    assert captured["host"] == "127.0.0.1"
    assert captured["api_key"] is None


def test_cli_playground_probe_is_data_free_and_does_not_follow_redirects(
    monkeypatch, capsys
):
    """The public shell probe must never attach or redirect a proxy bearer token."""
    import urllib.request

    from freellmpool.cli import main

    secret = "sentinel-playground-proxy-secret"
    monkeypatch.setenv("FREELLMPOOL_PROXY_KEY", secret)
    captured = {}

    class _Response:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Opener:
        def open(self, request, *, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _Response()

    def _build_opener(*handlers):
        captured["handlers"] = handlers
        return _Opener()

    monkeypatch.setattr(urllib.request, "build_opener", _build_opener)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("default redirect-following opener used")
        ),
    )

    assert main(["playground", "--port", "8181"]) == 0
    output = capsys.readouterr()
    assert output.out.strip() == "http://127.0.0.1:8181/playground"
    assert secret not in output.out + output.err
    assert captured["request"].get_header("Authorization") is None
    assert captured["timeout"] == 1.5
    assert captured["handlers"]
    redirect_handler = captured["handlers"][0]
    assert isinstance(redirect_handler, urllib.request.HTTPRedirectHandler)
    assert redirect_handler.redirect_request(None, None, 302, "", {}, "https://evil.invalid") is None
