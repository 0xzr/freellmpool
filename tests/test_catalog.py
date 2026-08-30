from __future__ import annotations

import json

import pytest

from freellmpool.catalog import (
    ExternalProvider,
    create_user_provider_stub,
    discover_openai_models,
    import_external_provider_to_user_catalog,
    load_external_catalog,
    match_local_provider,
    parse_external_catalog,
    suggest_external_provider,
    sync_external_catalog,
)


def test_parse_external_catalog_rate_limits():
    data = {
        "providers": [
            {
                "name": "Small",
                "category": "provider_api",
                "url": "https://example.test/small",
                "baseUrl": "https://api.example.test/v1",
                "description": "small plan",
                "models": [{"id": "a", "rateLimit": "10 RPM, 100 RPD"}],
            },
            {
                "name": "Large",
                "category": "provider_api",
                "url": "https://example.test/large",
                "baseUrl": "https://api2.example.test/v1",
                "description": "large plan",
                "models": [{"id": "b", "rateLimit": "2k RPM, 1M TPD"}],
            },
        ]
    }
    providers = parse_external_catalog(data)
    assert [p.name for p in providers] == ["Large", "Small"]
    assert providers[0].best_rpm == 2000
    assert providers[0].best_tpd == 1_000_000
    assert providers[1].best_rpd == 100


def test_suggest_external_provider_matches_typo_and_model_id():
    data = {
        "providers": [
            {
                "name": "Hyperbolic",
                "baseUrl": "https://api.hyperbolic.xyz/v1",
                "models": [{"id": "meta-llama/Llama-3.3-70B-Instruct", "rateLimit": "100 RPD"}],
            }
        ]
    }
    providers = parse_external_catalog(data)

    typo = suggest_external_provider("Hyperbolc", providers)
    by_model = suggest_external_provider("Llama-3.3-70B-Instruct", providers)

    assert typo is not None
    assert typo.provider.name == "Hyperbolic"
    assert not typo.exact
    assert by_model is not None
    assert by_model.provider.name == "Hyperbolic"
    assert by_model.exact


def test_import_external_provider_to_user_catalog(tmp_path, monkeypatch):
    from freellmpool.catalog import default_external_catalog_path

    cache = tmp_path / "provider_catalog.json"
    user_catalog = tmp_path / "providers.toml"
    cache.write_text(
        '{"providers":[{"name":"ModelScope","baseUrl":"https://api-inference.modelscope.cn/v1",'
        '"models":[{"id":"Qwen/Qwen3.5-27B","modality":"Text","rateLimit":"2,000 RPD total; <=500 RPD/model"},'
        '{"id":"+ API-Inference-enabled models","modality":"LLM","rateLimit":"Dynamic quotas"}]}]}'
    )
    monkeypatch.setenv("FREELLMPOOL_EXTERNAL_CATALOG_PATH", str(cache))
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(user_catalog))

    assert default_external_catalog_path() == cache
    local_id = import_external_provider_to_user_catalog("ModelScope")
    assert local_id == "modelscope"
    written = user_catalog.read_text()
    assert 'id = "modelscope"' in written
    assert 'key_env = "MODELSCOPE_API_KEY"' in written
    assert "Qwen/Qwen3.5-27B" in written
    assert "+ API-Inference-enabled models" not in written
    assert "rpd = 500" in written


def test_import_external_provider_missing_cache_points_to_catalog_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("FREELLMPOOL_EXTERNAL_CATALOG_PATH", str(tmp_path / "missing.json"))

    with pytest.raises(ValueError, match="freellmpool catalog sync"):
        import_external_provider_to_user_catalog("ModelScope")


def test_import_external_provider_not_found_sanitizes_and_bounds_suggestions(
    tmp_path, monkeypatch
):
    cache = tmp_path / "provider_catalog.json"
    malicious_name = "Trusted\x1b[31m\nFORGED\r\x07" + ("N" * 4_000)
    cache.write_text(
        json.dumps(
            {
                "providers": [
                    {"name": malicious_name},
                    *({"name": "P" + ("X" * 4_000)} for _ in range(10)),
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FREELLMPOOL_EXTERNAL_CATALOG_PATH", str(cache))

    with pytest.raises(ValueError) as exc_info:
        import_external_provider_to_user_catalog("missing\nquery")

    message = str(exc_info.value)
    assert "\\u001b" in message
    assert "\\u000a" in message
    assert not any(control in message for control in ("\x1b", "\n", "\r", "\x07"))
    assert len(message) <= 1_200


def test_match_local_provider_handles_missing_local_base_url():
    external = ExternalProvider(
        name="Demo",
        slug="external-demo",
        category=None,
        url=None,
        base_url="https://example.test/v1",
        description="",
        model_count=0,
        best_rpd=0,
        best_rpm=0,
        best_tpd=0,
        generous_score=0,
    )

    class LocalProvider:
        id = "local"
        label = "Local"
        base_url = None

    assert match_local_provider(external, [LocalProvider()]) is None


def test_create_user_provider_stub(tmp_path, monkeypatch):
    user_catalog = tmp_path / "providers.toml"
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(user_catalog))

    local_id = create_user_provider_stub(
        name="Hyperbolic",
        base_url="https://api.hyperbolic.xyz/v1/",
        model="meta-llama/Llama-3.3-70B-Instruct",
    )

    text = user_catalog.read_text()
    assert local_id == "hyperbolic"
    assert 'id = "hyperbolic"' in text
    assert 'base_url = "https://api.hyperbolic.xyz/v1"' in text
    assert 'key_env = "HYPERBOLIC_API_KEY"' in text
    assert 'name = "meta-llama/Llama-3.3-70B-Instruct"' in text


def test_discover_openai_models(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, *args):
            return b'{"data":[{"id":"model-a"},{"id":"model-b"}]}'

    seen = {}

    def fake_open(request, timeout=None):  # OpenerDirector.open signature
        seen["url"] = request.full_url
        seen["auth"] = request.headers.get("Authorization")
        seen["timeout"] = timeout
        return Response()

    # Discovery goes through the no-redirect opener (so the Bearer key can't be
    # forwarded to a 3xx target); patch its .open.
    from freellmpool import catalog

    monkeypatch.setattr(catalog._NO_REDIRECT_OPENER, "open", fake_open)

    models = discover_openai_models("https://api.example.test/v1/", api_key="secret", timeout=3)

    assert models == ["model-a", "model-b"]
    assert seen == {
        "url": "https://api.example.test/v1/models",
        "auth": "Bearer secret",
        "timeout": 3,
    }


def test_sync_external_catalog_does_not_follow_redirects(tmp_path, monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, *args):
            return b'{"providers": []}'

    from freellmpool import catalog

    seen = {}

    def safe_open(request, timeout=None):
        seen["url"] = request.full_url
        seen["accept"] = request.headers.get("Accept")
        seen["timeout"] = timeout
        return Response()

    def unsafe_open(*args, **kwargs):
        raise AssertionError("catalog sync bypassed the no-redirect opener")

    monkeypatch.setattr(catalog._NO_REDIRECT_OPENER, "open", safe_open)
    monkeypatch.setattr(catalog.urllib.request, "urlopen", unsafe_open)

    path, providers = sync_external_catalog(
        source_url="https://catalog.example.test/data.json",
        path=tmp_path / "catalog.json",
        timeout=3,
    )

    assert path.exists()
    assert providers == []
    assert seen == {
        "url": "https://catalog.example.test/data.json",
        "accept": "application/json",
        "timeout": 3,
    }


def _write_cache(tmp_path, monkeypatch, base_url, model_id="m"):
    cache = tmp_path / "provider_catalog.json"
    cache.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": "P",
                        "baseUrl": base_url,
                        "models": [{"id": model_id, "modality": "Text", "rateLimit": "1 RPD"}],
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("FREELLMPOOL_EXTERNAL_CATALOG_PATH", str(cache))
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(tmp_path / "providers.toml"))


def test_import_rejects_non_https_base_url(tmp_path, monkeypatch):
    _write_cache(tmp_path, monkeypatch, "http://api.insecure.test/v1")
    with pytest.raises(ValueError, match="https"):
        import_external_provider_to_user_catalog("P")


def test_import_rejects_surrounding_whitespace_base_url(tmp_path, monkeypatch):
    _write_cache(tmp_path, monkeypatch, "https://api.test/v1\n")
    with pytest.raises(ValueError, match="https"):
        import_external_provider_to_user_catalog("P")


def test_import_sanitizes_control_chars_in_model_id(tmp_path, monkeypatch):
    """A malicious model id with a newline must not corrupt the user catalog."""
    import tomllib

    evil = 'good\nkey_env = "PATH"\n[[provider]]\nid = "injected"\nmodels = [{ name = "x"'
    _write_cache(tmp_path, monkeypatch, "https://api.evil.test/v1", model_id=evil)
    import_external_provider_to_user_catalog("P")
    parsed = tomllib.loads((tmp_path / "providers.toml").read_text())
    assert [p.get("id") for p in parsed.get("provider", [])] == ["p"]


def test_create_user_provider_stub_rejects_bad_scheme(tmp_path, monkeypatch):
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(tmp_path / "providers.toml"))
    with pytest.raises(ValueError, match="http"):
        create_user_provider_stub(name="X", base_url="file:///etc/passwd", model="m")


def test_create_user_provider_stub_allows_http_localhost(tmp_path, monkeypatch):
    # http is fine for a user's own custom/local endpoint (e.g. Ollama).
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(tmp_path / "providers.toml"))
    local_id = create_user_provider_stub(
        name="Local", base_url="http://localhost:11434/v1", model="llama3"
    )
    assert local_id == "local"
    assert 'base_url = "http://localhost:11434/v1"' in (tmp_path / "providers.toml").read_text()


def test_discover_openai_models_rejects_non_http_scheme():
    with pytest.raises(ValueError, match="http"):
        discover_openai_models("file:///etc/passwd")


def _stub_capacity_dependencies(monkeypatch):
    class EmptyReport:
        healthy_count = 0
        low_quota_count = 0
        needs_action = False
        providers = []

    monkeypatch.setattr("freellmpool.cli.load_catalog", lambda: [])
    monkeypatch.setattr("freellmpool.key_inventory.load_inventory", lambda: [])
    monkeypatch.setattr(
        "freellmpool.capacity.build_capacity_report", lambda **_kwargs: EmptyReport()
    )


def test_capacity_status_sanitizes_and_bounds_untrusted_cached_catalog_fields(
    tmp_path, monkeypatch, capsys
):
    from freellmpool.cli import main

    cache = tmp_path / "provider_catalog.json"
    malicious_name = "Trusted\x1b[31m\nFORGED-NAME\r" + ("N" * 4_000)
    malicious_url = (
        "https://catalog.example.test/\x1b]8;;https://evil.test\x07\nFORGED-URL"
        + ("U" * 5_000)
    )
    cache.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": malicious_name,
                        "url": malicious_url,
                        "baseUrl": "https://api.example.test/v1\x1b[2J",
                        "models": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FREELLMPOOL_EXTERNAL_CATALOG_PATH", str(cache))
    _stub_capacity_dependencies(monkeypatch)

    providers = load_external_catalog(cache)
    assert len(providers) == 1
    assert len(providers[0].name) <= 128
    assert providers[0].url is not None and len(providers[0].url) <= 2_048

    assert (
        main(
            [
                "capacity",
                "status",
                "--all",
                "--target",
                "0",
                "--no-catalog-sync",
                "--external-limit",
                "1",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "\x07" not in out
    assert "\r" not in out
    assert "\nFORGED-NAME" not in out
    assert "\nFORGED-URL" not in out
    external_line = next(line for line in out.splitlines() if "external    " in line)
    assert len(external_line) <= 2_300


def test_capacity_status_treats_non_utf8_cached_catalog_as_empty(
    tmp_path, monkeypatch, capsys
):
    from freellmpool.cli import main

    cache = tmp_path / "provider_catalog.json"
    cache.write_bytes(b'{"providers": [{"name": "Safe"}]}\xff')
    monkeypatch.setenv("FREELLMPOOL_EXTERNAL_CATALOG_PATH", str(cache))
    _stub_capacity_dependencies(monkeypatch)

    assert load_external_catalog(cache) == []
    assert main(["capacity", "status", "--all", "--target", "0"]) == 0
    captured = capsys.readouterr()
    assert "External catalog cache: 0 providers" in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_load_external_catalog_ignores_invalid_provider_container(tmp_path):
    cache = tmp_path / "provider_catalog.json"
    cache.write_text('{"providers": 7}', encoding="utf-8")

    assert load_external_catalog(cache) == []
