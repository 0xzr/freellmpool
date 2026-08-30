"""Opt-in, loopback-only local OpenAI-compatible runtime discovery."""

from __future__ import annotations

import http.client
import json
import os
import shlex
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from freellmpool.cli import main
from freellmpool.local_runtime import (
    LocalRuntime,
    canonical_loopback_base_url,
    discover_runtime,
    import_runtime,
    remove_runtime,
)


@pytest.mark.parametrize(
    "url",
    (
        "http://localhost:1234/v1",
        "http://127.1:1234/v1",
        "http://2130706433:1234/v1",
        "http://0x7f000001:1234/v1",
        "http://0.0.0.0:1234/v1",
        "http://192.168.1.4:1234/v1",
        "http://169.254.1.2:1234/v1",
        "http://[::ffff:127.0.0.1]:1234/v1",
        "http://user@127.0.0.1:1234/v1",
        "https://127.0.0.1:1234/v1",
        "file:///tmp/models",
    ),
)
def test_local_runtime_rejects_every_noncanonical_or_nonloopback_target(url):
    with pytest.raises(ValueError, match="loopback"):
        canonical_loopback_base_url(url)


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("http://127.0.0.1:1234/v1/", "http://127.0.0.1:1234/v1"),
        ("http://127.99.2.3:8080/v1", "http://127.99.2.3:8080/v1"),
        ("http://[::1]:8080/v1", "http://[::1]:8080/v1"),
    ),
)
def test_local_runtime_accepts_only_canonical_literal_loopback(raw, expected):
    assert canonical_loopback_base_url(raw) == expected


class _Response:
    def __init__(self, payload: bytes, *, server: str = "lm-studio") -> None:
        self.payload = payload
        self.headers = {"Server": server}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


def test_discovery_is_bounded_headerless_get_with_redirects_disabled(monkeypatch):
    from freellmpool import local_runtime

    seen: dict[str, object] = {}

    def open_request(request, timeout):
        seen.update(
            url=request.full_url,
            method=request.get_method(),
            authorization=request.headers.get("Authorization"),
            timeout=timeout,
        )
        return _Response(b'{"data":[{"id":"qwen-local"},{"id":"coder-local"}]}')

    monkeypatch.setattr(local_runtime._NO_REDIRECT_OPENER, "open", open_request)
    result = discover_runtime(
        name="lm_studio",
        base_url="http://127.0.0.1:1234/v1",
        timeout=0.25,
    )

    assert result == LocalRuntime(
        provider_id="local_lm_studio",
        label="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        models=("qwen-local", "coder-local"),
    )
    assert seen == {
        "url": "http://127.0.0.1:1234/v1/models",
        "method": "GET",
        "authorization": None,
        "timeout": 0.25,
    }


def test_discovery_opener_ignores_environment_proxies():
    env = dict(os.environ)
    env.update(
        {
            "http_proxy": "http://192.0.2.10:3128",
            "https_proxy": "http://192.0.2.10:3128",
            "HTTP_PROXY": "http://192.0.2.10:3128",
            "HTTPS_PROXY": "http://192.0.2.10:3128",
        }
    )
    env.pop("no_proxy", None)
    env.pop("NO_PROXY", None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import urllib.request; "
                "from freellmpool.local_runtime import _NO_REDIRECT_OPENER; "
                "handlers = [h for h in _NO_REDIRECT_OPENER.handlers "
                "if isinstance(h, urllib.request.ProxyHandler)]; "
                "assert not any(h.proxies for h in handlers), handlers"
            ),
        ],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert check.returncode == 0, check.stderr


def test_discovery_rejects_self_oversize_and_unsafe_models(monkeypatch):
    from freellmpool import local_runtime

    monkeypatch.setattr(
        local_runtime._NO_REDIRECT_OPENER,
        "open",
        lambda *_args, **_kwargs: _Response(b'{"data":[]}', server="freellmpool/0.13.0"),
    )
    with pytest.raises(ValueError, match="freellmpool proxy"):
        discover_runtime(name="llama_cpp", base_url="http://127.0.0.1:8080/v1")

    monkeypatch.setattr(
        local_runtime._NO_REDIRECT_OPENER,
        "open",
        lambda *_args, **_kwargs: _Response(b"x" * (1_048_576 + 1)),
    )
    with pytest.raises(ValueError, match="exceeds"):
        discover_runtime(name="lm_studio", base_url="http://127.0.0.1:1234/v1")

    monkeypatch.setattr(
        local_runtime._NO_REDIRECT_OPENER,
        "open",
        lambda *_args, **_kwargs: _Response(b'{"data":[{"id":"bad\\nmodel"}]}'),
    )
    with pytest.raises(ValueError, match="unsafe model"):
        discover_runtime(name="lm_studio", base_url="http://127.0.0.1:1234/v1")


@pytest.mark.parametrize("model", ("$(touch /tmp/freellmpool-pwned)", "`id`"))
def test_discovery_rejects_shell_metacharacters_in_model_ids(monkeypatch, model):
    from freellmpool import local_runtime

    monkeypatch.setattr(
        local_runtime._NO_REDIRECT_OPENER,
        "open",
        lambda *_args, **_kwargs: _Response(
            json.dumps({"data": [{"id": model}]}).encode("utf-8")
        ),
    )

    with pytest.raises(ValueError, match="unsafe model"):
        discover_runtime(name="lm_studio", base_url="http://127.0.0.1:1234/v1")


def test_discovery_sanitizes_malformed_http_protocol_errors(monkeypatch):
    from freellmpool import local_runtime

    monkeypatch.setattr(
        local_runtime._NO_REDIRECT_OPENER,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(http.client.BadStatusLine("secret")),
    )
    with pytest.raises(ValueError, match="BadStatusLine") as opened:
        discover_runtime(name="lm_studio", base_url="http://127.0.0.1:1234/v1")
    assert "secret" not in str(opened.value)

    class _IncompleteResponse(_Response):
        def read(self, limit: int) -> bytes:
            raise http.client.IncompleteRead(b"secret")

    monkeypatch.setattr(
        local_runtime._NO_REDIRECT_OPENER,
        "open",
        lambda *_args, **_kwargs: _IncompleteResponse(b""),
    )
    with pytest.raises(ValueError, match="IncompleteRead") as read:
        discover_runtime(name="lm_studio", base_url="http://127.0.0.1:1234/v1")
    assert "secret" not in str(read.value)


@pytest.mark.parametrize("model", ("$(touch /tmp/freellmpool-pwned)", "`id`"))
def test_import_rejects_shell_metacharacters_in_model_ids(tmp_path, model):
    path = tmp_path / "providers.toml"
    runtime = LocalRuntime(
        provider_id="local_lm_studio",
        label="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        models=(model,),
    )

    with pytest.raises(ValueError, match="unsafe local runtime model"):
        import_runtime(runtime, path=path)
    assert not path.exists()


def test_import_is_pin_only_atomic_idempotent_and_reversible(tmp_path, monkeypatch):
    path = tmp_path / "providers.toml"
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(path))
    runtime = LocalRuntime(
        provider_id="local_lm_studio",
        label="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        models=("qwen-local", "coder-local"),
    )

    assert import_runtime(runtime) == path
    first = path.read_text(encoding="utf-8")
    assert 'id = "local_lm_studio"' in first
    assert 'base_url = "http://127.0.0.1:1234/v1"' in first
    assert "local = true" in first
    assert 'auth = "none"' in first
    assert first.count("auto = false") == 2
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.chmod(0o644)
    assert import_runtime(runtime) == path
    assert path.read_text(encoding="utf-8") == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    assert remove_runtime("local_lm_studio") is True
    assert "local_lm_studio" not in path.read_text(encoding="utf-8")
    assert remove_runtime("local_lm_studio") is False


def test_imported_loopback_provider_loads_without_broad_local_network_opt_in(
    tmp_path, monkeypatch
):
    from freellmpool.config import configured_providers, load_catalog

    path = tmp_path / "providers.toml"
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(path))
    monkeypatch.delenv("FREELLMPOOL_ALLOW_LOCAL_PROVIDERS", raising=False)
    runtime = LocalRuntime(
        provider_id="local_lm_studio",
        label="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        models=("qwen-local",),
    )

    import_runtime(runtime)
    provider = next(item for item in load_catalog() if item.id == runtime.provider_id)

    assert provider.base_url == runtime.base_url
    assert provider.auth == "none"
    assert provider.models[0].name == "qwen-local"
    assert provider.models[0].auto is False
    assert provider in configured_providers([provider], {})


def test_doctor_accepts_a_managed_loopback_provider_after_import(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "providers.toml"
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(path))
    monkeypatch.setenv("FREELLMPOOL_CONFIG_FILE", str(tmp_path / "config.toml"))
    monkeypatch.setenv("FREELLMPOOL_QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setenv("FREELLMPOOL_CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setenv("FREELLMPOOL_EXTERNAL_CATALOG_PATH", str(tmp_path / "external.json"))
    runtime = LocalRuntime(
        provider_id="local_ollama",
        label="Ollama",
        base_url="http://127.0.0.1:11434/v1",
        models=("llama-local",),
    )

    import_runtime(runtime)

    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "catalog: ok" in output
    assert "base_url must be https" not in output


def test_import_refuses_to_overwrite_an_unmanaged_provider(tmp_path, monkeypatch):
    path = tmp_path / "providers.toml"
    path.write_text(
        '[[provider]]\nid = "local_lm_studio"\nbase_url = "https://example.test/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(path))
    runtime = LocalRuntime(
        provider_id="local_lm_studio",
        label="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        models=("qwen-local",),
    )
    with pytest.raises(ValueError, match="unmanaged provider"):
        import_runtime(runtime)


@pytest.mark.parametrize(
    "catalog",
    (
        "[[provider]]\nid = 'local_lm_studio' # literal string plus comment\n",
        (
            "[[provider]]\nid = 'unrelated'\n"
            "[[provider]]\nid = \"local_lm_studio\" # second array entry\n"
        ),
    ),
)
def test_import_parses_unmanaged_provider_ids_with_quotes_and_comments(
    tmp_path, catalog
):
    path = tmp_path / "providers.toml"
    path.write_text(catalog, encoding="utf-8")
    runtime = LocalRuntime(
        provider_id="local_lm_studio",
        label="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        models=("qwen-local",),
    )

    with pytest.raises(ValueError, match="unmanaged provider"):
        import_runtime(runtime, path=path)
    assert path.read_text(encoding="utf-8") == catalog


def test_import_rejects_unmanaged_duplicate_beside_managed_block(tmp_path):
    path = tmp_path / "providers.toml"
    runtime = LocalRuntime(
        provider_id="local_lm_studio",
        label="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        models=("qwen-local",),
    )
    import_runtime(runtime, path=path)
    duplicate = "[[provider]]\nid = 'local_lm_studio' # unmanaged duplicate\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(duplicate)
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="unmanaged provider"):
        import_runtime(runtime, path=path)
    assert path.read_text(encoding="utf-8") == before


def test_import_fails_closed_on_invalid_existing_toml(tmp_path):
    path = tmp_path / "providers.toml"
    invalid = "[[provider]\nid = 'local_lm_studio'\n"
    path.write_text(invalid, encoding="utf-8")
    runtime = LocalRuntime(
        provider_id="local_lm_studio",
        label="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        models=("qwen-local",),
    )

    with pytest.raises(ValueError, match="invalid TOML"):
        import_runtime(runtime, path=path)
    assert path.read_text(encoding="utf-8") == invalid


def test_concurrent_imports_preserve_both_catalog_updates(tmp_path, monkeypatch):
    from freellmpool import local_runtime

    path = tmp_path / "providers.toml"
    first = LocalRuntime(
        provider_id="local_lm_studio",
        label="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        models=("qwen-local",),
    )
    second = LocalRuntime(
        provider_id="local_ollama",
        label="Ollama",
        base_url="http://127.0.0.1:11434/v1",
        models=("llama-local",),
    )
    barrier = threading.Barrier(2)
    original_render = local_runtime._render

    def synchronized_render(runtime):
        rendered = original_render(runtime)
        barrier.wait(timeout=5)
        return rendered

    monkeypatch.setattr(local_runtime, "_render", synchronized_render)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(import_runtime, runtime, path=path)
            for runtime in (first, second)
        ]
        for future in futures:
            assert future.result(timeout=5) == path

    catalog = path.read_text(encoding="utf-8")
    assert 'id = "local_lm_studio"' in catalog
    assert 'id = "local_ollama"' in catalog


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory locking regression")
def test_catalog_lock_replacement_does_not_create_parallel_lock_domain(tmp_path, monkeypatch):
    import fcntl

    from freellmpool import local_runtime

    path = tmp_path / "providers.toml"
    lock_path = tmp_path / ".providers.toml.lock"
    second_attempted = threading.Event()
    second_contended = threading.Event()
    second_acquired = threading.Event()
    second_thread_id = None
    original_lock_file = local_runtime._lock_file

    def observe_second_lock(fd):
        if threading.get_ident() != second_thread_id:
            original_lock_file(fd)
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            second_contended.set()
            second_attempted.set()
            original_lock_file(fd)
        else:
            second_attempted.set()

    def acquire_replacement_lock():
        nonlocal second_thread_id
        second_thread_id = threading.get_ident()
        with local_runtime._catalog_lock(path):
            second_acquired.set()

    monkeypatch.setattr(local_runtime, "_lock_file", observe_second_lock)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with local_runtime._catalog_lock(path):
            lock_path.rename(tmp_path / ".providers.toml.lock.replaced")
            future = executor.submit(acquire_replacement_lock)
            assert second_attempted.wait(timeout=5)
            assert second_contended.is_set()
            assert not second_acquired.is_set()

        assert second_acquired.wait(timeout=5)
        future.result(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX inode substitution regression")
def test_catalog_lock_fails_closed_on_inode_substitution(tmp_path, monkeypatch):
    from freellmpool import local_runtime

    path = tmp_path / "providers.toml"
    lock_path = tmp_path / ".providers.toml.lock"
    replaced_path = tmp_path / ".providers.toml.lock.replaced"
    original_lock_file = local_runtime._lock_file
    substituted = False

    def substitute_after_lock(fd):
        nonlocal substituted
        original_lock_file(fd)
        if stat.S_ISREG(os.fstat(fd).st_mode):
            lock_path.rename(replaced_path)
            lock_path.write_text("replacement", encoding="utf-8")
            substituted = True

    monkeypatch.setattr(local_runtime, "_lock_file", substitute_after_lock)

    with pytest.raises(ValueError, match="changed during lock acquisition"):
        with local_runtime._catalog_lock(path):
            pytest.fail("substituted catalog lock must not be admitted")
    assert substituted


def test_import_refuses_existing_final_path_symlink(tmp_path):
    target = tmp_path / "actual.toml"
    target.write_text("sentinel\n", encoding="utf-8")
    path = tmp_path / "providers.toml"
    path.symlink_to(target)
    runtime = LocalRuntime(
        provider_id="local_lm_studio",
        label="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        models=("qwen-local",),
    )

    with pytest.raises(ValueError, match="symlink"):
        import_runtime(runtime, path=path)
    assert path.is_symlink()
    assert target.read_text(encoding="utf-8") == "sentinel\n"


def test_import_refuses_symlinked_parent_directory(tmp_path):
    actual_parent = tmp_path / "actual"
    actual_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(actual_parent, target_is_directory=True)
    path = linked_parent / "providers.toml"
    runtime = LocalRuntime(
        provider_id="local_lm_studio",
        label="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        models=("qwen-local",),
    )

    with pytest.raises(ValueError, match="parent directory symlink"):
        import_runtime(runtime, path=path)
    assert not (actual_parent / "providers.toml").exists()


def test_cli_discover_is_preview_only_and_import_requires_affirmation(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "providers.toml"
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(path))
    runtime = LocalRuntime(
        provider_id="local_lm_studio",
        label="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        models=("qwen-local",),
    )
    monkeypatch.setattr("freellmpool.local_runtime.discover_runtime", lambda **_kwargs: runtime)

    assert main(["local", "discover", "--name", "lm_studio", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["provider_id"] == "local_lm_studio"
    assert payload["models"] == ["qwen-local"]
    assert not path.exists()

    assert main(["local", "import", "--name", "lm_studio"]) == 2
    assert "--yes" in capsys.readouterr().err
    assert not path.exists()

    assert main(["local", "import", "--name", "lm_studio", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "pin-only" in out
    assert "freellmpool ask --providers local_lm_studio --model qwen-local hello" in out
    assert "freellmpool local remove local_lm_studio --yes" in out
    assert path.exists()


@pytest.mark.parametrize("model", ("$(touch /tmp/freellmpool-pwned)", "`id`"))
def test_cli_import_shell_quotes_the_example_command(
    tmp_path, monkeypatch, capsys, model
):
    from freellmpool import local_runtime

    path = tmp_path / "providers.toml"
    runtime = LocalRuntime(
        provider_id="local_lm_studio",
        label="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        models=(model,),
    )
    monkeypatch.setattr(local_runtime, "discover_runtime", lambda **_kwargs: runtime)
    monkeypatch.setattr(local_runtime, "import_runtime", lambda _runtime: path)

    assert main(["local", "import", "--name", "lm_studio", "--yes"]) == 0
    output = capsys.readouterr().out
    command = shlex.join(
        [
            "freellmpool",
            "ask",
            "--providers",
            runtime.provider_id,
            "--model",
            model,
            "hello",
        ]
    )
    assert f"  {command}\n" in output


def test_cli_remove_only_managed_local_runtime(tmp_path, monkeypatch, capsys):
    path = tmp_path / "providers.toml"
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(path))
    runtime = LocalRuntime(
        provider_id="local_ollama",
        label="Ollama",
        base_url="http://127.0.0.1:11434/v1",
        models=("llama-local",),
    )
    import_runtime(runtime)

    assert main(["local", "remove", "local_ollama"]) == 2
    assert "--yes" in capsys.readouterr().err
    assert main(["local", "remove", "local_ollama", "--yes"]) == 0
    assert "Removed local_ollama" in capsys.readouterr().out


def test_cli_import_sanitizes_catalog_filesystem_failures(monkeypatch, capsys):
    from freellmpool import local_runtime

    secret = "sentinel-private-catalog-path"
    runtime = LocalRuntime(
        provider_id="local_ollama",
        label="Ollama",
        base_url="http://127.0.0.1:11434/v1",
        models=("llama-local",),
    )
    monkeypatch.setattr(local_runtime, "discover_runtime", lambda **_kwargs: runtime)

    def _fail_import(_runtime):
        raise PermissionError(secret)

    monkeypatch.setattr(local_runtime, "import_runtime", _fail_import)

    assert main(["local", "import", "--name", "ollama", "--yes"]) == 3
    captured = capsys.readouterr()
    assert "local catalog update failed (PermissionError)" in captured.err
    assert secret not in captured.out + captured.err


def test_cli_remove_sanitizes_catalog_filesystem_failures(monkeypatch, capsys):
    from freellmpool import local_runtime

    secret = "sentinel-private-lock-path"

    def _fail_remove(_provider_id):
        raise OSError(secret)

    monkeypatch.setattr(local_runtime, "remove_runtime", _fail_remove)

    assert main(["local", "remove", "local_ollama", "--yes"]) == 3
    captured = capsys.readouterr()
    assert "local catalog update failed (OSError)" in captured.err
    assert secret not in captured.out + captured.err
