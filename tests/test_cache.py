"""Response cache + Pool integration."""

from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest
from helpers import make_post, make_stream_post

from freellmpool.cache import Cache
from freellmpool.router import Pool


def test_cache_get_put_and_ttl(tmp_path):
    t = [100.0]
    c = Cache(ttl=50.0, path=tmp_path / "c.db", clock=lambda: t[0])
    key = c.make_key([{"role": "user", "content": "hi"}], None, None, 1024, 0.0, None)
    assert c.get(key) is None
    c.put(key, {"text": "cached"})
    assert c.get(key)["text"] == "cached"
    t[0] = 200.0  # 100s later, ttl 50 → expired
    assert c.get(key) is None


def test_cache_uses_wal_and_prunes_to_max_entries(tmp_path):
    t = [100.0]
    c = Cache(ttl=999.0, path=tmp_path / "c.db", clock=lambda: t[0], max_entries=2)
    with sqlite3.connect(c.path) as con:
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    for key in ("k1", "k2", "k3"):
        c.put(key, {"text": key})
        t[0] += 1
    with sqlite3.connect(c.path) as con:
        rows = con.execute("SELECT key FROM cache ORDER BY created").fetchall()
    assert [row[0] for row in rows] == ["k2", "k3"]


def test_cache_concurrent_get_put_is_best_effort(tmp_path):
    c = Cache(ttl=999.0, path=tmp_path / "c.db", max_entries=100)

    def worker(n):
        for i in range(25):
            key = f"{n}-{i}"
            c.put(key, {"text": key})
            assert c.get(key) in (None, {"text": key})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with sqlite3.connect(c.path) as con:
        count = con.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    assert count <= 100


def test_cache_connection_errors_are_best_effort(tmp_path, monkeypatch):
    c = Cache(ttl=999.0, path=tmp_path / "c.db")

    def broken_conn():
        raise sqlite3.OperationalError("disk unavailable")

    monkeypatch.setattr(c, "_conn", broken_conn)
    assert c.get("missing") is None
    c.put("k", {"text": "ok"})  # must not raise


def test_make_key_includes_tool_choice():
    args = ([{"role": "user", "content": "hi"}], None, None, 1024, 0.0, [{"type": "function"}])
    k_auto = Cache.make_key(*args, "auto")
    k_req = Cache.make_key(*args, "required")
    assert k_auto != k_req  # different tool_choice → different cache entry


def test_make_key_includes_routing():
    # a per-request routing override must not collide with a different mode's cache:
    # a "quality" ask should never be served a cached "fast"-routed reply.
    args = ([{"role": "user", "content": "hi"}], None, None, 1024, 0.0, None, None)
    assert Cache.make_key(*args, routing="fast") != Cache.make_key(*args, routing="quality")
    # the same effective mode still shares a bucket
    assert Cache.make_key(*args, routing="fair") == Cache.make_key(*args, routing="fair")


def test_make_key_includes_response_format_and_protocol():
    args = ([{"role": "user", "content": "hi"}], None, None, 1024, 0.0, None, None)
    plain = Cache.make_key(*args, routing="fair")
    structured = Cache.make_key(
        *args,
        routing="fair",
        response_format={"type": "json_object"},
    )
    responses = Cache.make_key(*args, routing="fair", protocol="responses")
    messages = Cache.make_key(*args, routing="fair", protocol="anthropic_messages")

    assert len({plain, structured, responses, messages}) == 4


def test_pool_uses_cache(providers, env, quota, tmp_path):
    cache = Cache(ttl=999.0, path=tmp_path / "c.db")
    post = make_post({})  # returns "ok", counts calls
    pool = Pool(providers, quota=quota, env=env, post=post, cache=cache)

    r1 = pool.ask("hello")
    assert r1.text == "ok" and not r1.cached
    n_after_first = len(post.calls)

    r2 = pool.ask("hello")  # identical → served from cache, no new provider call
    assert r2.text == "ok" and r2.cached
    assert r2.provider_id == r1.provider_id  # cached reply preserves the original provider
    assert len(post.calls) == n_after_first  # no extra network call
    assert pool.stats["cache_hits"] == 1


def test_feature_cache_hit_is_rejected_after_conformance_regression(
    providers, env, quota, tmp_path
):
    from freellmpool.conformance import (
        FEATURE_TOOLS,
        STATUS_PASS,
        STATUS_UNSUPPORTED,
        ConformanceStore,
    )
    alpha, beta = providers[:2]
    alpha_model = next(model for model in alpha.models if model.enabled)
    beta_model = next(model for model in beta.models if model.enabled)
    store = ConformanceStore(tmp_path / "conformance.json")
    for provider, model in ((alpha, alpha_model), (beta, beta_model)):
        store.record(
            provider,
            model.name,
            FEATURE_TOOLS,
            status=STATUS_PASS,
            classification="verified",
        )
    cache = Cache(ttl=999.0, path=tmp_path / "c.db")
    post = make_post({})
    pool = Pool(
        [alpha, beta],
        quota=quota,
        env=env,
        post=post,
        cache=cache,
        conformance=store,
    )
    tools = [{"type": "function", "function": {"name": "answer", "parameters": {}}}]

    assert pool.chat([{"role": "user", "content": "answer"}], tools=tools).cached is False
    assert post.calls[0]["url"].startswith("https://alpha.test/")
    assert len(post.calls) == 1
    store.record(
        alpha,
        alpha_model.name,
        FEATURE_TOOLS,
        status=STATUS_UNSUPPORTED,
        classification="unsupported",
    )

    reply = pool.chat([{"role": "user", "content": "answer"}], tools=tools)
    assert reply.cached is False
    assert reply.provider_id == "beta"
    assert len(post.calls) == 2


def test_async_feature_cache_hit_is_rejected_after_conformance_regression(
    providers, env, quota, tmp_path
):
    from freellmpool.aio import AsyncPool
    from freellmpool.client import HTTPResult
    from freellmpool.conformance import (
        FEATURE_TOOLS,
        STATUS_PASS,
        STATUS_UNSUPPORTED,
        ConformanceStore,
    )
    from freellmpool.errors import NoProvidersConfigured

    provider = providers[0]
    model = next(model for model in provider.models if model.enabled)
    store = ConformanceStore(tmp_path / "conformance.json")
    store.record(
        provider,
        model.name,
        FEATURE_TOOLS,
        status=STATUS_PASS,
        classification="verified",
    )
    cache = Cache(ttl=999.0, path=tmp_path / "c.db")
    calls = []

    async def apost(url, headers, body, timeout):
        calls.append(url)
        return HTTPResult(
            200,
            {"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
            "",
        )

    pool = AsyncPool(
        Pool(
            [provider],
            quota=quota,
            env=env,
            cache=cache,
            conformance=store,
        ),
        apost=apost,
    )
    tools = [{"type": "function", "function": {"name": "answer", "parameters": {}}}]

    first = asyncio.run(
        pool.achat([{"role": "user", "content": "answer"}], tools=tools)
    )
    assert first.cached is False
    store.record(
        provider,
        model.name,
        FEATURE_TOOLS,
        status=STATUS_UNSUPPORTED,
        classification="unsupported",
    )

    with pytest.raises(NoProvidersConfigured):
        asyncio.run(pool.achat([{"role": "user", "content": "answer"}], tools=tools))
    assert len(calls) == 1


def test_cache_preserves_tool_calls(providers, env, quota, tmp_path):
    tc = [{"id": "c", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    post = make_post(
        {
            "alpha.test": (
                200,
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": None, "tool_calls": tc}}
                    ]
                },
            )
        }
    )
    cache = Cache(ttl=999.0, path=tmp_path / "c.db")
    pool = Pool(providers, quota=quota, env=env, post=post, cache=cache)
    tools = [{"type": "function", "function": {"name": "f"}}]
    pool.ask("hi", providers=["alpha"], tools=tools)
    n = len(post.calls)
    r2 = pool.ask("hi", providers=["alpha"], tools=tools)  # identical → cached
    assert r2.cached
    assert r2.message["tool_calls"] == tc  # tool_calls survive the round-trip through cache
    assert len(post.calls) == n  # no new provider call


def test_streaming_bypasses_cache(providers, env, quota, tmp_path):
    cache = Cache(ttl=999.0, path=tmp_path / "c.db")
    sp = make_stream_post({})
    pool = Pool(providers, quota=quota, env=env, post=make_post({}), stream_post=sp, cache=cache)
    list(pool.stream_chat([{"role": "user", "content": "hi"}], providers=["alpha"]))
    list(pool.stream_chat([{"role": "user", "content": "hi"}], providers=["alpha"]))
    assert len(sp.calls) == 2  # streaming is not cached — both hit the provider
    assert pool.stats["cache_hits"] == 0


def test_cache_distinguishes_prompts(providers, env, quota, tmp_path):
    cache = Cache(ttl=999.0, path=tmp_path / "c.db")
    post = make_post({})
    pool = Pool(providers, quota=quota, env=env, post=post, cache=cache)
    pool.ask("first")
    pool.ask("second")  # different prompt → not a cache hit
    assert pool.stats["cache_hits"] == 0


def test_cache_disabled_by_default(providers, env, quota):
    post = make_post({})
    pool = Pool(providers, quota=quota, env=env, post=post)  # no cache
    pool.ask("hello")
    pool.ask("hello")
    assert len(post.calls) == 2  # both hit the provider
