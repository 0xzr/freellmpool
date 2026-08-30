"""Router selection + failover behavior."""

from __future__ import annotations

import pytest
from helpers import gemini_body, make_post, make_stream_post, openai_body

from freellmpool.errors import AllProvidersExhausted, NoProvidersConfigured
from freellmpool.models import Model, Provider
from freellmpool.router import Pool


def test_ask_returns_first_success(providers, env, quota):
    post = make_post({})  # everything returns 200 "ok"
    pool = Pool(providers, quota=quota, env=env, post=post)
    reply = pool.ask("hello")
    assert reply.text == "ok"
    assert len(post.calls) == 1  # stopped at the first success


def test_failover_skips_429(providers, env, quota):
    post = make_post(
        {
            "alpha.test": (429, {"error": {"message": "rate limited"}}),
            "beta.test": (200, openai_body("from beta")),
        }
    )
    pool = Pool(providers, quota=quota, env=env, post=post)
    reply = pool.ask("hello", providers=["alpha", "beta"])
    assert reply.text == "from beta"
    assert reply.provider_id == "beta"
    # alpha-small 429s → alpha's other model is skipped this request → beta wins.
    # So only 2 calls (alpha-small, beta), not 3.
    assert len(post.calls) == 2


def test_all_exhausted_raises(providers, env, quota):
    post = make_post(
        {
            "alpha.test": (500, {}),
            "beta.test": (503, {}),
            "gee.test": (500, {}),
            "free.test": (500, {}),
        }
    )
    pool = Pool(providers, quota=quota, env=env, post=post)
    with pytest.raises(AllProvidersExhausted) as exc:
        pool.ask("hello")
    assert exc.value.attempts  # every target recorded a reason


def test_no_providers_configured():
    pool = Pool([], env={})
    with pytest.raises(NoProvidersConfigured):
        pool.ask("hello")


def test_pool_owned_quota_uses_bounded_success_batch_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("FREELLMPOOL_QUOTA_PATH", str(tmp_path / "quota.json"))

    default_pool = Pool([], env={})
    configured_pool = Pool(
        [],
        env={
            "FREELLMPOOL_QUOTA_FLUSH_EVERY": "7",
            "FREELLMPOOL_QUOTA_FLUSH_INTERVAL": "0.25",
        },
    )

    assert default_pool.quota.flush_every == 32
    assert default_pool.quota.flush_interval == 1.0
    assert configured_pool.quota.flush_every == 7
    assert configured_pool.quota.flush_interval == 0.25


def test_least_used_first_ordering(providers, env, quota):
    post = make_post({})
    pool = Pool(providers, quota=quota, env=env, post=post)
    # Pre-load alpha usage so beta should be picked first.
    quota.record("alpha", "alpha-small", 5)
    quota.record("alpha", "alpha-big", 5)
    reply = pool.ask("hello")
    assert reply.provider_id != "alpha"  # not the heavily-used alpha


def test_over_budget_sinks_to_back(providers, env, quota):
    # alpha-small has rpd=2; record 2 so it is over budget and other models win.
    quota.record("alpha", "alpha-small", 2)
    post = make_post({})
    pool = Pool(providers, quota=quota, env=env, post=post)
    reply = pool.ask("hi", model="alpha-small", providers=["alpha"])
    # only candidate is the over-budget one → still served (best-effort), recorded 3rd
    assert reply.text == "ok"
    assert quota.used("alpha", "alpha-small") == 3


def test_model_filter(providers, env, quota):
    post = make_post({})
    pool = Pool(providers, quota=quota, env=env, post=post)
    pool.ask("hi", model="beta-1")
    assert all("beta.test" in c["url"] for c in post.calls)


def test_gemini_adapter_shape(providers, env, quota):
    post = make_post({"gee.test": (200, gemini_body("hi from gemini"))})
    pool = Pool(providers, quota=quota, env=env, post=post)
    reply = pool.ask("hello", system="be terse", providers=["gee"])
    assert reply.text == "hi from gemini"
    body = post.calls[0]["body"]
    assert "contents" in body and "systemInstruction" in body  # gemini shape
    assert post.calls[0]["headers"].get("x-goog-api-key") == "g"


def test_keyless_provider_sends_no_auth_header(providers, env, quota):
    post = make_post({"free.test": (200, openai_body("free!"))})
    # empty env: only the keyless provider is usable
    pool = Pool(providers, quota=quota, env={}, post=post)
    reply = pool.ask("hello", providers=["free"])
    assert reply.text == "free!"
    assert reply.provider_id == "free"
    assert "Authorization" not in post.calls[0]["headers"]


def test_429_triggers_cooldown(providers, env, quota):
    post = make_post(
        {
            "alpha.test": (429, {"error": {"message": "slow down"}}),
            "beta.test": (200, openai_body("beta")),
        }
    )
    pool = Pool(
        providers, quota=quota, env=env, post=post, cooldown_seconds=60.0, clock=lambda: 100.0
    )
    r1 = pool.ask("hi", providers=["alpha", "beta"])
    assert r1.provider_id == "beta"
    assert pool._cooldown_until["alpha"] == 160.0  # 100 + 60s cooldown


def test_cooldown_deprioritizes_within_window(providers, env, quota):
    post = make_post({"alpha.test": (429, {}), "beta.test": (200, openai_body("beta"))})
    pool = Pool(
        providers, quota=quota, env=env, post=post, cooldown_seconds=60.0, clock=lambda: 20.0
    )
    pool.ask("hi", providers=["alpha", "beta"])  # alpha 429 → cooled until t=80
    # at t=20 alpha is still cooling; even though it's now usable + least-used,
    # beta is tried first because alpha is in its cooldown window.
    pool._post = make_post(
        {"alpha.test": (200, openai_body("alpha")), "beta.test": (200, openai_body("beta"))}
    )
    r2 = pool.ask("hi", providers=["alpha", "beta"])
    assert r2.provider_id == "beta"  # alpha deprioritized despite being usable now


def test_empty_completion_is_failure(providers, env, quota):
    post = make_post({"alpha.test": (200, openai_body("")), "beta.test": (200, openai_body("x"))})
    pool = Pool(providers, quota=quota, env=env, post=post)
    reply = pool.ask("hi", providers=["alpha", "beta"])
    assert reply.provider_id == "beta"  # empty alpha skipped


def test_stream_chat_yields_meta_then_deltas(providers, env, quota):
    sp = make_stream_post({"alpha.test": (200, ["Hel", "lo"])})
    pool = Pool(providers, quota=quota, env=env, stream_post=sp)
    gen = pool.stream_chat([{"role": "user", "content": "hi"}], providers=["alpha"])
    meta = next(gen)
    assert meta["provider"] == "alpha"
    assert "".join(gen) == "Hello"


def test_stream_chat_failover_before_first_byte(providers, env, quota):
    sp = make_stream_post({"alpha.test": (500, []), "beta.test": (200, ["ok"])})
    pool = Pool(providers, quota=quota, env=env, stream_post=sp)
    gen = pool.stream_chat([{"role": "user", "content": "hi"}], providers=["alpha", "beta"])
    meta = next(gen)
    assert meta["provider"] == "beta"  # alpha 500 → failed over before streaming
    assert meta["attempts"] == len(sp.calls)
    assert meta["attempts"] > 1
    assert "".join(gen) == "ok"


def test_stream_chat_account_quota_skips_provider_catalog_and_backs_off(
    providers, env, quota
):
    sp = make_stream_post(
        {
            "alpha.test": (402, ["You have depleted your monthly included credits."]),
            "beta.test": (200, ["ok"]),
        }
    )
    pool = Pool(providers[:2], quota=quota, env=env, stream_post=sp)
    messages = [{"role": "user", "content": "hi"}]

    first = pool.stream_chat(messages)
    assert next(first)["provider"] == "beta"
    assert "".join(first) == "ok"
    assert sum("alpha.test" in call["url"] for call in sp.calls) == 1
    assert pool.cooldown_snapshot(pool._clock())["alpha"] > 0

    sp.calls.clear()
    second = pool.stream_chat(messages)
    assert next(second)["provider"] == "beta"
    assert "beta.test" in sp.calls[0]["url"]


@pytest.mark.parametrize(
    "error_body",
    [
        {"error": {"type": "insufficient_funds", "message": "Insufficient funds"}},
        {},
        {"error": {"type": "future_budget_error", "message": "changed wording"}},
    ],
)
def test_vercel_any_402_backs_off_the_whole_provider(env, quota, error_body):
    vercel = Provider(
        id="vercel",
        label="Vercel",
        adapter="openai",
        base_url="https://ai-gateway.vercel.sh/v1",
        key_env="AI_GATEWAY_API_KEY",
        models=(Model("free-a", rpd=0), Model("free-b", rpd=0)),
    )
    fallback = Provider(
        id="beta",
        label="Beta",
        adapter="openai",
        base_url="https://beta.test/v1",
        key_env="BETA_KEY",
        models=(Model("beta-1", rpd=0),),
    )
    post = make_post(
        {
            "ai-gateway.vercel.sh": (
                402,
                error_body,
            ),
            "beta.test": (200, openai_body("fallback")),
        }
    )
    pool = Pool(
        [vercel, fallback],
        quota=quota,
        env={**env, "AI_GATEWAY_API_KEY": "secret"},
        post=post,
    )

    assert pool.ask("hi").provider_id == "beta"
    assert sum("ai-gateway.vercel.sh" in call["url"] for call in post.calls) == 1
    assert pool.cooldown_snapshot(pool._clock())["vercel"] > 0

    post.calls.clear()
    assert pool.ask("again").provider_id == "beta"
    assert "beta.test" in post.calls[0]["url"]


def test_non_vercel_capability_402_remains_model_local(env, quota):
    provider = Provider(
        id="alpha",
        label="Alpha",
        adapter="openai",
        base_url="https://alpha.test/v1",
        key_env="ALPHA_KEY",
        models=(Model("paid-only", rpd=0), Model("included", rpd=0)),
    )

    def response(_url, _headers, body):
        if body["model"] == "paid-only":
            return 402, {
                "error": {"type": "insufficient_funds", "message": "Insufficient funds"}
            }
        return 200, openai_body("included")

    post = make_post({"alpha.test": response})
    pool = Pool([provider], quota=quota, env=env, post=post)
    reply = pool.ask("hi")

    assert reply.model == "included"
    assert len(post.calls) == 2
    assert "alpha" not in pool.cooldown_snapshot(pool._clock())


def test_stream_chat_uses_one_overall_failover_timeout(providers, env, quota):
    clock = {"now": 0.0}
    seen: list[float] = []

    def stream_post(url, headers, body, timeout):
        seen.append(timeout)
        clock["now"] += 40.0
        return 503, iter(())

    pool = Pool(
        providers[:2],
        quota=quota,
        env=env,
        stream_post=stream_post,
        clock=lambda: clock["now"],
    )
    gen = pool.stream_chat(
        [{"role": "user", "content": "hi"}],
        timeout=60.0,
    )

    with pytest.raises(AllProvidersExhausted):
        next(gen)
    assert seen == [60.0, 20.0]


def test_stream_chat_skips_gemini(providers, env, quota):
    # 'gee' is a gemini-adapter provider → excluded from streaming
    sp = make_stream_post({})
    pool = Pool(providers, quota=quota, env=env, stream_post=sp)
    gen = pool.stream_chat([{"role": "user", "content": "hi"}], providers=["gee"])
    with pytest.raises(NoProvidersConfigured):
        next(gen)


def test_cooldown_expires_and_provider_reeligible(providers, env, quota):
    t = [0.0]
    post = make_post({"alpha.test": (429, {}), "beta.test": (200, openai_body("beta"))})
    pool = Pool(
        providers, quota=quota, env=env, post=post, cooldown_seconds=60.0, clock=lambda: t[0]
    )
    pool.ask("hi", providers=["alpha", "beta"])  # alpha 429 at t=0 → cooled until t=60
    assert pool._cooldown_until["alpha"] == 60.0
    # advance the clock past the cooldown window; alpha works again now
    t[0] = 61.0
    pool._post = make_post(
        {"alpha.test": (200, openai_body("alpha")), "beta.test": (200, openai_body("beta"))}
    )
    r = pool.ask("hi", providers=["alpha", "beta"])
    assert r.provider_id == "alpha"  # no longer cooled + least-used → tried first


def test_stream_chat_skips_disabled_model(env, quota):
    from freellmpool.models import Model, Provider

    prov = Provider(
        id="x",
        label="X",
        adapter="openai",
        base_url="https://x.test/v1",
        key_env="X_KEY",
        models=(Model("on"), Model("off", enabled=False)),
    )
    sp = make_stream_post({})
    pool = Pool([prov], quota=quota, env={"X_KEY": "k"}, stream_post=sp)
    gen = pool.stream_chat([{"role": "user", "content": "hi"}])  # auto
    assert next(gen)["model"] == "on"
    list(gen)
    assert len(sp.calls) == 1  # disabled model never hit
    # explicit pin can still stream the disabled one
    gen2 = pool.stream_chat([{"role": "user", "content": "hi"}], model="off")
    assert next(gen2)["model"] == "off"


def test_disabled_model_skipped_by_auto_but_reachable_explicitly(env, quota):
    from freellmpool.models import Model, Provider

    prov = Provider(
        id="x",
        label="X",
        adapter="openai",
        base_url="https://x.test/v1",
        key_env="X_KEY",
        models=(Model("on-model"), Model("off-model", enabled=False)),
    )
    post = make_post({})  # any call returns "ok"
    pool = Pool([prov], quota=quota, env={"X_KEY": "k"}, post=post)
    # auto routing only ever picks the enabled model
    seen = set()
    for _ in range(5):
        seen.add(pool.ask("hi").model)
    assert seen == {"on-model"}
    # but an explicit pin can still reach the disabled one
    assert pool.ask("hi", model="off-model").model == "off-model"


def test_all_targets_uses_indexed_filters(env, quota):
    from freellmpool.models import Model, Provider

    a = Provider(
        id="a",
        label="A",
        adapter="openai",
        base_url="https://a.test/v1",
        auth="none",
        models=(Model("shared"), Model("off", enabled=False)),
    )
    b = Provider(
        id="b",
        label="B",
        adapter="openai",
        base_url="https://b.test/v1",
        auth="none",
        models=(Model("shared"),),
    )
    pool = Pool([a, b], quota=quota, env=env, post=make_post({}))

    assert [t.name for t in pool._all_targets()] == ["a/shared", "b/shared"]
    assert [t.name for t in pool._all_targets(model="off")] == ["a/off"]
    assert [t.name for t in pool._all_targets(include=["b"], model="shared")] == ["b/shared"]


def test_exact_pin_preserves_alias_excluded_from_automatic_routing(env, quota):
    from freellmpool.models import Model, Provider

    provider = Provider(
        id="aliases",
        label="Aliases",
        adapter="openai",
        base_url="https://aliases.test/v1",
        auth="none",
        models=(Model("canonical"), Model("friendly-alias", auto=False)),
    )
    pool = Pool([provider], quota=quota, env=env, post=make_post({}))

    assert [target.name for target in pool._all_targets()] == ["aliases/canonical"]
    assert [target.name for target in pool._all_targets(model="friendly-alias")] == [
        "aliases/friendly-alias"
    ]
    assert pool.ask("hi", model="friendly-alias").model == "friendly-alias"


def test_tool_calls_reply_is_success(providers, env, quota):
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
    pool = Pool(providers, quota=quota, env=env, post=post)
    reply = pool.ask(
        "hi", providers=["alpha"], tools=[{"type": "function", "function": {"name": "f"}}]
    )
    assert reply.message["tool_calls"] == tc  # empty content but tool_calls → success
    assert reply.attempts == 1


def test_freellmpoolerror_rename_and_alias():
    # the base exception is FreeLLMPoolError; BuffetError stays as a back-compat alias
    from freellmpool import FreeLLMPoolError
    from freellmpool.errors import AllProvidersExhausted, BuffetError

    assert BuffetError is FreeLLMPoolError
    assert issubclass(AllProvidersExhausted, FreeLLMPoolError)


def _diversity_providers():
    from freellmpool.models import Model, Provider

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


def test_unpinned_chat_tries_distinct_provider_before_transport_retry(
    quota, monkeypatch
):
    """A pooled request spends its second attempt on diversity, not alpha again."""
    from freellmpool import client as client_module
    from freellmpool.client import HTTPResult

    calls: list[str] = []

    def post_once(url, headers, body, timeout, deadline):
        provider = "alpha" if "alpha.test" in url else "beta"
        calls.append(provider)
        if provider == "alpha":
            return HTTPResult(503, {"error": "down"}, "down")
        return HTTPResult(200, openai_body("beta"), "ok")

    monkeypatch.setattr(client_module, "_post_once", post_once)
    monkeypatch.setattr(client_module, "_RETRY_BACKOFF_S", 0.0)
    pool = Pool(_diversity_providers(), quota=quota, env={})

    assert pool.chat([{"role": "user", "content": "hi"}], model="shared").provider_id == "beta"
    assert calls == ["alpha", "beta"]


def test_unpinned_chat_retries_original_only_after_distinct_providers(
    quota, monkeypatch
):
    from freellmpool import client as client_module
    from freellmpool.client import HTTPResult

    calls: list[str] = []

    def post_once(url, headers, body, timeout, deadline):
        provider = "alpha" if "alpha.test" in url else "beta"
        calls.append(provider)
        if calls == ["alpha", "beta", "alpha"]:
            return HTTPResult(200, openai_body("recovered"), "ok")
        return HTTPResult(503, {"error": "down"}, "down")

    monkeypatch.setattr(client_module, "_post_once", post_once)
    monkeypatch.setattr(client_module, "_RETRY_BACKOFF_S", 0.0)
    pool = Pool(_diversity_providers(), quota=quota, env={})

    reply = pool.chat([{"role": "user", "content": "hi"}], model="shared")
    assert reply.provider_id == "alpha"
    assert calls == ["alpha", "beta", "alpha"]


def test_exact_pin_retains_same_target_transport_retry(quota, monkeypatch):
    from freellmpool import client as client_module
    from freellmpool.client import HTTPResult

    calls: list[str] = []

    def post_once(url, headers, body, timeout, deadline):
        calls.append(url)
        if len(calls) == 1:
            return HTTPResult(503, {"error": "down"}, "down")
        return HTTPResult(200, openai_body("ok"), "ok")

    monkeypatch.setattr(client_module, "_post_once", post_once)
    monkeypatch.setattr(client_module, "_RETRY_BACKOFF_S", 0.0)
    pool = Pool(_diversity_providers(), quota=quota, env={})

    reply = pool.chat(
        [{"role": "user", "content": "hi"}],
        model="shared",
        providers=["alpha"],
    )
    assert reply.provider_id == "alpha"
    assert len(calls) == 2


def test_retry_after_sleep_is_deferred_until_distinct_provider_attempt(
    quota, monkeypatch
):
    from freellmpool import client as client_module
    from freellmpool.client import HTTPResult

    events: list[str] = []

    def post_once(url, headers, body, timeout, deadline):
        provider = "alpha" if "alpha.test" in url else "beta"
        events.append(provider)
        if events == ["alpha", "beta", "sleep", "alpha"]:
            return HTTPResult(200, openai_body("ok"), "ok")
        if provider == "alpha":
            return HTTPResult(
                429,
                {"error": "slow"},
                "slow",
                headers={"Retry-After": "0.001"},
            )
        return HTTPResult(503, {"error": "down"}, "down")

    monkeypatch.setattr(client_module, "_post_once", post_once)
    monkeypatch.setattr(client_module, "_RETRY_BACKOFF_S", 0.0)
    monkeypatch.setattr("freellmpool.router.time.sleep", lambda delay: events.append("sleep"))
    pool = Pool(_diversity_providers(), quota=quota, env={})

    assert pool.chat([{"role": "user", "content": "hi"}], model="shared").text == "ok"
    assert events == ["alpha", "beta", "sleep", "alpha"]


def test_local_pool_timeout_fails_over_without_poisoning_provider(
    quota, tmp_path, monkeypatch
):
    import httpx

    from freellmpool import client as client_module
    from freellmpool.client import HTTPResult
    from freellmpool.route_health import RouteHealthStore

    calls: list[str] = []

    def post_once(url, headers, body, timeout, deadline):
        provider = "alpha" if "alpha.test" in url else "beta"
        calls.append(provider)
        if provider == "alpha":
            raise httpx.PoolTimeout("local connection pool saturated")
        return HTTPResult(200, openai_body("beta"), "ok")

    health = RouteHealthStore(path=tmp_path / "health.json")
    monkeypatch.setattr(client_module, "_post_once", post_once)
    monkeypatch.setattr(client_module, "_RETRY_BACKOFF_S", 0.0)
    pool = Pool(
        _diversity_providers(), quota=quota, env={}, route_health=health
    )

    assert pool.chat([{"role": "user", "content": "hi"}], model="shared").provider_id == "beta"
    alpha = health.state("alpha/shared")
    assert calls == ["alpha", "beta"]
    assert alpha is not None and alpha.failures == 0 and alpha.state == "closed"


def test_local_pool_timeout_releases_expired_open_probe_before_failover_wins(
    quota, tmp_path, monkeypatch
):
    import httpx

    from freellmpool import client as client_module
    from freellmpool.client import HTTPResult
    from freellmpool.route_health import RouteHealthStore

    now = [100.0]
    health = RouteHealthStore(
        path=tmp_path / "health.json",
        clock=lambda: now[0],
        failure_threshold=1,
        base_cooldown=1,
    )
    for key in ("alpha/shared", "beta/shared"):
        health.record_failure(key, "availability")
    now[0] = 102.0

    def post_once(url, headers, body, timeout, deadline):
        if "alpha.test" in url:
            raise httpx.PoolTimeout("local connection pool saturated")
        return HTTPResult(200, openai_body("beta"), "ok")

    monkeypatch.setattr(client_module, "_post_once", post_once)
    monkeypatch.setattr(client_module, "_RETRY_BACKOFF_S", 0.0)
    pool = Pool(
        _diversity_providers(),
        quota=quota,
        env={},
        route_health=health,
        clock=lambda: now[0],
    )

    assert pool.chat([{"role": "user", "content": "hi"}], model="shared").provider_id == "beta"
    alpha = health.state("alpha/shared")
    assert alpha is not None
    assert alpha.failures == 1
    assert alpha.state == "open"
    assert alpha.half_open_until == 0
    assert health.acquire_many(("alpha/shared",)) is not None


def test_deferred_local_pool_timeout_reacquires_fresh_probe_before_retry(
    quota, tmp_path, monkeypatch
):
    import httpx

    from freellmpool import client as client_module
    from freellmpool.client import HTTPResult
    from freellmpool.route_health import RouteHealthStore

    now = [100.0]
    health = RouteHealthStore(
        path=tmp_path / "health.json",
        clock=lambda: now[0],
        failure_threshold=1,
        base_cooldown=1,
    )
    for key in ("alpha/shared", "beta/shared"):
        health.record_failure(key, "availability")
    now[0] = 102.0
    calls: list[str] = []

    def post_once(url, headers, body, timeout, deadline):
        provider = "alpha" if "alpha.test" in url else "beta"
        calls.append(provider)
        if calls == ["alpha"]:
            raise httpx.PoolTimeout("local connection pool saturated")
        if provider == "beta":
            return HTTPResult(503, {"error": "down"}, "down")
        return HTTPResult(200, openai_body("recovered"), "ok")

    monkeypatch.setattr(client_module, "_post_once", post_once)
    monkeypatch.setattr(client_module, "_RETRY_BACKOFF_S", 0.0)
    pool = Pool(
        _diversity_providers(),
        quota=quota,
        env={},
        route_health=health,
        clock=lambda: now[0],
    )

    reply = pool.chat([{"role": "user", "content": "hi"}], model="shared")
    alpha = health.state("alpha/shared")
    assert reply.provider_id == "alpha"
    assert calls == ["alpha", "beta", "alpha"]
    assert alpha is not None and alpha.state == "closed"
    assert alpha.failures == 1 and alpha.successes == 1


def test_deferred_failure_is_persisted_immediately_even_if_alternative_wins(
    quota, tmp_path, monkeypatch
):
    from freellmpool import client as client_module
    from freellmpool.client import HTTPResult
    from freellmpool.route_health import RouteHealthStore

    calls: list[str] = []

    def post_once(url, headers, body, timeout, deadline):
        provider = "alpha" if "alpha.test" in url else "beta"
        calls.append(provider)
        if provider == "alpha":
            return HTTPResult(503, {"error": "down"}, "down")
        return HTTPResult(200, openai_body("beta"), "ok")

    health = RouteHealthStore(path=tmp_path / "health.json")
    monkeypatch.setattr(client_module, "_post_once", post_once)
    monkeypatch.setattr(client_module, "_RETRY_BACKOFF_S", 0.0)
    pool = Pool(
        _diversity_providers(), quota=quota, env={}, route_health=health
    )

    assert pool.chat([{"role": "user", "content": "hi"}], model="shared").provider_id == "beta"
    assert calls == ["alpha", "beta"]
    assert RouteHealthStore(path=tmp_path / "health.json").state("alpha/shared").failures == 1


def test_authorized_retry_can_recover_newly_opened_route(quota, tmp_path, monkeypatch):
    from freellmpool import client as client_module
    from freellmpool.client import HTTPResult
    from freellmpool.route_health import RouteHealthStore

    calls: list[str] = []

    def post_once(url, headers, body, timeout, deadline):
        provider = "alpha" if "alpha.test" in url else "beta"
        calls.append(provider)
        if calls == ["alpha", "beta", "alpha"]:
            return HTTPResult(200, openai_body("recovered"), "ok")
        return HTTPResult(503, {"error": "down"}, "down")

    health = RouteHealthStore(
        path=tmp_path / "health.json", failure_threshold=1, base_cooldown=60
    )
    monkeypatch.setattr(client_module, "_post_once", post_once)
    monkeypatch.setattr(client_module, "_RETRY_BACKOFF_S", 0.0)
    pool = Pool(
        _diversity_providers(), quota=quota, env={}, route_health=health
    )

    assert pool.chat([{"role": "user", "content": "hi"}], model="shared").text == "recovered"
    row = RouteHealthStore(path=tmp_path / "health.json").state("alpha/shared")
    assert calls == ["alpha", "beta", "alpha"]
    assert row.state == "closed"
    assert row.failures == 1 and row.successes == 1
