"""AsyncPool: async failover, gemini shape, and shared metrics/quota bookkeeping.

Driven with asyncio.run so no pytest-asyncio dependency is needed.
"""

from __future__ import annotations

import asyncio

import pytest
from helpers import gemini_body, make_post

from freellmpool import client as sync_client
from freellmpool.aio import AsyncPool
from freellmpool.errors import AllProvidersExhausted, ProviderHTTPError
from freellmpool.models import Model, Provider
from freellmpool.router import Pool


def _async_post(script):
    """Adapt the sync fake transport into an async one (await apost(...))."""
    sync = make_post(script)

    async def apost(url, headers, body, timeout):
        return sync(url, headers, body, timeout)

    apost.calls = sync.calls
    return apost


def test_aask_succeeds(providers, env, quota):
    pool = AsyncPool(Pool(providers, quota=quota, env=env), apost=_async_post({}))
    reply = asyncio.run(pool.aask("hi"))
    assert reply.text == "ok"
    assert reply.provider_id in {p.id for p in providers}


def test_async_failover_skips_500(providers, env, quota):
    apost = _async_post({"alpha.test": (500, {"error": "boom"})})
    pool = AsyncPool(Pool(providers, quota=quota, env=env), apost=apost)
    reply = asyncio.run(
        pool.achat([{"role": "user", "content": "hi"}], providers=["alpha", "beta"])
    )
    assert reply.provider_id == "beta"  # alpha 500'd, failed over to beta
    assert pool.metrics.get("alpha/alpha-small").fail >= 1
    assert pool.metrics.get("beta/beta-1").ok == 1


def test_async_account_quota_exhaustion_deprioritizes_provider(providers, env, quota):
    calls = []

    async def apost(url, headers, body, timeout):
        calls.append(url)
        if "alpha.test" in url:
            return sync_client.HTTPResult(
                402,
                {"error": "You have depleted your monthly included credits."},
                "",
            )
        return sync_client.HTTPResult(
            200,
            {"choices": [{"message": {"content": "ok"}}]},
            "",
        )

    pool = AsyncPool(Pool(providers[:2], quota=quota, env=env), apost=apost)
    assert asyncio.run(pool.aask("first")).provider_id == "beta"
    calls.clear()

    assert asyncio.run(pool.aask("second")).provider_id == "beta"
    assert "beta.test" in calls[0]


def test_async_account_quota_is_not_surfaced_as_client_error(providers, env, quota):
    apost = _async_post(
        {"test": (402, {"error": "You have depleted your monthly included credits."})}
    )
    pool = AsyncPool(Pool(providers[:2], quota=quota, env=env), apost=apost)

    with pytest.raises(AllProvidersExhausted) as exc_info:
        asyncio.run(pool.achat([{"role": "user", "content": "hi"}]))

    assert exc_info.value.client_status is None


def test_async_chat_uses_one_overall_failover_timeout(providers, env, quota):
    clock = {"now": 0.0}
    seen: list[float] = []

    async def apost(url, headers, body, timeout):
        seen.append(timeout)
        clock["now"] += 40.0
        return sync_client.HTTPResult(503, {"error": "down"}, "down")

    pool = AsyncPool(
        Pool(providers[:2], quota=quota, env=env, clock=lambda: clock["now"]),
        apost=apost,
    )

    with pytest.raises(AllProvidersExhausted):
        asyncio.run(pool.achat([{"role": "user", "content": "hi"}], timeout=60.0))
    assert seen == [60.0, 20.0]


def test_async_surfaces_nonretryable_client_error(providers, env, quota):
    apost = _async_post({"test": (400, {"error": {"message": "bad request"}})})
    pool = AsyncPool(Pool(providers, quota=quota, env=env), apost=apost)

    with pytest.raises(AllProvidersExhausted) as exc_info:
        asyncio.run(pool.achat([{"role": "user", "content": "hi"}], providers=["alpha", "beta"]))

    assert exc_info.value.client_status == 400
    assert "bad request" in (exc_info.value.client_message or "")


def test_async_gemini_shape(providers, env, quota):
    apost = _async_post({"gee.test": (200, gemini_body("hi from gemini"))})
    pool = AsyncPool(Pool(providers, quota=quota, env=env), apost=apost)
    reply = asyncio.run(pool.achat([{"role": "user", "content": "hi"}], providers=["gee"]))
    assert reply.text == "hi from gemini"


@pytest.mark.parametrize("model", ["gemini-3.6-flash", "gemini-3.7-flash"])
def test_async_gemini_36_and_37_omit_sampling_and_receive_thinking_headroom(model, quota):
    provider = Provider(
        id="gemini",
        label="Gemini",
        adapter="gemini",
        base_url="https://gee.test",
        key_env="GEMINI_API_KEY",
        models=(Model(model),),
    )
    apost = _async_post({"gee.test": (200, gemini_body("ok"))})
    pool = AsyncPool(Pool([provider], quota=quota, env={"GEMINI_API_KEY": "g"}), apost=apost)

    reply = asyncio.run(
        pool.achat(
            [{"role": "user", "content": "hi"}],
            model=model,
            max_tokens=512,
            temperature=0.7,
        )
    )

    assert reply.text == "ok"
    assert apost.calls[0]["body"]["generationConfig"] == {"maxOutputTokens": 4096}


def test_async_records_quota_and_stats(providers, env, quota):
    pool = AsyncPool(Pool(providers, quota=quota, env=env), apost=_async_post({}))
    asyncio.run(pool.aask("hi"))
    assert pool.stats["requests"] == 1


def test_async_context_manager_closes(providers, env, quota):
    async def run():
        async with AsyncPool(Pool(providers, quota=quota, env=env), apost=_async_post({})) as pool:
            return await pool.aask("hi")

    reply = asyncio.run(run())
    assert reply.text == "ok"


def test_async_client_disables_redirects(providers, env, quota, monkeypatch):
    import httpx

    seen = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        async def aclose(self):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    pool = AsyncPool(Pool(providers, quota=quota, env=env))

    async def run():
        await pool._client_obj()
        await pool.aclose()

    asyncio.run(run())
    assert seen["follow_redirects"] is False


def test_async_no_providers_raises(quota):
    from freellmpool.errors import NoProvidersConfigured

    pool = AsyncPool(Pool([], quota=quota, env={}), apost=_async_post({}))
    try:
        asyncio.run(pool.aask("hi"))
        raise AssertionError("expected NoProvidersConfigured")
    except NoProvidersConfigured:
        pass


def test_async_uses_response_cache(providers, env, quota, tmp_path):
    from freellmpool.cache import Cache

    apost = _async_post({})
    cache = Cache(ttl=60, path=tmp_path / "cache.sqlite")  # isolated, not the shared default
    pool = AsyncPool(Pool(providers, quota=quota, env=env, cache=cache), apost=apost)

    async def run():
        first = await pool.aask("same question")
        second = await pool.aask("same question")
        return first, second

    first, second = asyncio.run(run())
    assert first.cached is False
    assert second.cached is True  # served from cache, no second upstream call
    assert pool.stats["cache_hits"] == 1
    # only one real upstream call happened
    assert len(apost.calls) == 1


def test_async_cache_key_includes_pool_routing(providers, env, quota, tmp_path):
    from freellmpool.cache import Cache

    cache = Cache(ttl=60, path=tmp_path / "cache.sqlite")
    fast_post = _async_post({})
    fast = AsyncPool(
        Pool(providers, quota=quota, env=env, cache=cache, routing="fast"),
        apost=fast_post,
    )
    asyncio.run(fast.aask("same question", providers=["alpha", "beta"]))
    assert len(fast_post.calls) == 1

    quality_post = _async_post({})
    quality = AsyncPool(
        Pool(providers, quota=quota, env=env, cache=cache, routing="quality"),
        apost=quality_post,
    )
    reply = asyncio.run(quality.aask("same question", providers=["alpha", "beta"]))

    assert reply.cached is False
    assert len(quality_post.calls) == 1
    assert quality.stats["cache_hits"] == 0


def test_async_custom_adapter_runs_via_thread(quota):
    from freellmpool import plugins
    from freellmpool.models import Model, Provider, Reply

    plugins._reset_for_tests()
    try:

        def my_adapter(provider, model, messages, **kw):
            return Reply(text="from-plugin", provider_id=provider.id, model=model, raw={})

        plugins.register_adapter("weird", my_adapter)
        prov = Provider(
            id="w",
            label="W",
            adapter="weird",
            base_url="https://w.test",
            auth="none",
            models=(Model("w-1"),),
        )
        pool = AsyncPool(Pool([prov], quota=quota, env={}), apost=_async_post({}))
        reply = asyncio.run(pool.aask("hi"))
        assert reply.text == "from-plugin"  # plugin adapter reached on the async path
    finally:
        plugins._reset_for_tests()


class _AsyncResp:
    def __init__(self, chunks, *, status=200, headers=None):
        self._chunks = chunks
        self.status_code = status
        self.headers = headers or {"content-type": "application/json"}

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _AsyncCM:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        return False


def test_async_default_apost_streams_response(providers, env, quota):
    class Client:
        def stream(self, *args, **kwargs):
            return _AsyncCM(_AsyncResp([b'{"choices":[{"message":{"content":"ok"}}]}']))

    pool = AsyncPool(Pool(providers, quota=quota, env=env))

    async def client_obj():
        return Client()

    pool._client_obj = client_obj
    result = asyncio.run(pool._apost("https://x.test/v1", {}, {}, 30.0))
    assert result.body["choices"][0]["message"]["content"] == "ok"


def test_async_default_apost_caps_oversized_response(providers, env, quota, monkeypatch):
    class Client:
        def stream(self, *args, **kwargs):
            return _AsyncCM(_AsyncResp([b"xx", b"xx"]))

    pool = AsyncPool(Pool(providers, quota=quota, env=env))

    async def client_obj():
        return Client()

    pool._client_obj = client_obj
    monkeypatch.setattr(sync_client, "_MAX_RESPONSE_BYTES", 3)
    with pytest.raises(ProviderHTTPError):
        asyncio.run(pool._apost("https://x.test/v1", {}, {}, 30.0))


def test_async_default_apost_retries_with_original_request_headers(providers, env, quota):
    calls = []
    request_headers = {"Authorization": "Bearer k", "Content-Type": "application/json"}

    class Client:
        def stream(self, *args, **kwargs):
            calls.append(dict(kwargs["headers"]))
            if len(calls) == 1:
                return _AsyncCM(
                    _AsyncResp(
                        [b'{"error":"slow"}'],
                        status=429,
                        headers={"Retry-After": "0", "x-response": "provider"},
                    )
                )
            return _AsyncCM(_AsyncResp([b'{"choices":[{"message":{"content":"ok"}}]}']))

    pool = AsyncPool(Pool(providers, quota=quota, env=env))

    async def client_obj():
        return Client()

    pool._client_obj = client_obj
    result = asyncio.run(pool._apost("https://x.test/v1", request_headers, {}, 30.0))
    assert result.status == 200
    assert calls == [request_headers, request_headers]


def _async_diversity_providers():
    return [
        Provider(
            id=provider_id,
            label=provider_id.upper(),
            adapter="openai",
            base_url=f"https://{provider_id}.test/v1",
            auth="none",
            models=(Model("shared"),),
        )
        for provider_id in ("alpha", "beta")
    ]


def test_async_unpinned_chat_tries_distinct_provider_before_retry(
    quota, tmp_path, monkeypatch
):
    from freellmpool.route_health import RouteHealthStore

    calls: list[str] = []

    class Client:
        def stream(self, method, url, **kwargs):
            provider = "alpha" if "alpha.test" in url else "beta"
            calls.append(provider)
            status = 503 if provider == "alpha" else 200
            body = (
                b'{"error":"down"}'
                if status != 200
                else b'{"choices":[{"message":{"content":"ok"}}]}'
            )
            return _AsyncCM(_AsyncResp([body], status=status))

    health = RouteHealthStore(path=tmp_path / "health.json")
    pool = AsyncPool(
        Pool(
            _async_diversity_providers(),
            quota=quota,
            env={},
            route_health=health,
        )
    )

    async def client_obj():
        return Client()

    pool._client_obj = client_obj
    monkeypatch.setattr(sync_client, "_RETRY_BACKOFF_S", 0.0)

    reply = asyncio.run(
        pool.achat([{"role": "user", "content": "hi"}], model="shared")
    )
    assert reply.provider_id == "beta"
    assert calls == ["alpha", "beta"]
    assert RouteHealthStore(path=tmp_path / "health.json").state("alpha/shared").failures == 1


def test_async_unpinned_chat_retries_original_after_alternatives(
    quota, tmp_path, monkeypatch
):
    from freellmpool.route_health import RouteHealthStore

    calls: list[str] = []

    class Client:
        def stream(self, method, url, **kwargs):
            provider = "alpha" if "alpha.test" in url else "beta"
            calls.append(provider)
            success = calls == ["alpha", "beta", "alpha"]
            body = (
                b'{"choices":[{"message":{"content":"ok"}}]}'
                if success
                else b'{"error":"down"}'
            )
            return _AsyncCM(_AsyncResp([body], status=200 if success else 503))

    health = RouteHealthStore(
        path=tmp_path / "health.json", failure_threshold=1, base_cooldown=60
    )
    pool = AsyncPool(
        Pool(
            _async_diversity_providers(),
            quota=quota,
            env={},
            route_health=health,
        )
    )

    async def client_obj():
        return Client()

    pool._client_obj = client_obj
    monkeypatch.setattr(sync_client, "_RETRY_BACKOFF_S", 0.0)

    reply = asyncio.run(
        pool.achat([{"role": "user", "content": "hi"}], model="shared")
    )
    assert reply.provider_id == "alpha"
    assert calls == ["alpha", "beta", "alpha"]
    alpha = RouteHealthStore(path=tmp_path / "health.json").state("alpha/shared")
    assert alpha.state == "closed"
    assert alpha.failures == 1 and alpha.successes == 1


def test_async_local_pool_timeout_fails_over_without_poisoning(
    quota, tmp_path, monkeypatch
):
    import httpx

    from freellmpool.route_health import RouteHealthStore

    calls: list[str] = []

    async def apost(url, headers, body, timeout):
        provider = "alpha" if "alpha.test" in url else "beta"
        calls.append(provider)
        if provider == "alpha":
            raise httpx.PoolTimeout("local connection pool saturated")
        return sync_client.HTTPResult(
            200,
            {"choices": [{"message": {"content": "ok"}}]},
            "ok",
        )

    health = RouteHealthStore(path=tmp_path / "health.json")
    pool = AsyncPool(
        Pool(
            _async_diversity_providers(),
            quota=quota,
            env={},
            route_health=health,
        ),
        apost=apost,
    )
    monkeypatch.setattr(sync_client, "_RETRY_BACKOFF_S", 0.0)

    reply = asyncio.run(
        pool.achat([{"role": "user", "content": "hi"}], model="shared")
    )
    alpha = health.state("alpha/shared")
    assert reply.provider_id == "beta"
    assert calls == ["alpha", "beta"]
    assert alpha is not None and alpha.failures == 0 and alpha.state == "closed"


def test_async_close_flushes_underlying_pool_telemetry(tmp_path):
    from freellmpool.quota import QuotaStore
    from freellmpool.route_health import RouteHealthStore
    from freellmpool.stats import StatsStore

    quota = QuotaStore(path=tmp_path / "quota.json", flush_every=100)
    stats = StatsStore(tmp_path / "stats.json", flush_every=100)
    health = RouteHealthStore(
        path=tmp_path / "health.json",
        success_flush_every=100,
        success_flush_interval=60,
    )
    apost = _async_post({})
    pool = AsyncPool(
        Pool(
            _async_diversity_providers(),
            quota=quota,
            env={},
            stats_store=stats,
            route_health=health,
        ),
        apost=apost,
    )

    async def run():
        await pool.aask("hello")
        await pool.aclose()

    asyncio.run(run())
    assert (tmp_path / "quota.json").exists()
    assert (tmp_path / "stats.json").exists()
    assert (tmp_path / "health.json").exists()
