"""Metrics-aware routing: fair mode sinks failing targets, fast mode sorts by latency."""

from __future__ import annotations

from helpers import make_post, make_stream_post

from freellmpool import capability as _capability
from freellmpool.client import HTTPResult
from freellmpool.models import Model, Provider
from freellmpool.router import Pool

_EASY = [{"role": "user", "content": "hi"}]
_HARD = [
    {
        "role": "user",
        "content": "Debug and refactor this algorithm:\n```python\ndef f():\n  pass\n```\n"
        "Explain step by step why it is slow.",
    }
]


def _names(pool, **kw):
    return [t.name for t in pool._order(pool._all_targets(**kw))]


def _quality_pool(tmp_path, monkeypatch, quota, *, scores, models):
    """A quality-routing pool over ``models`` with an injected capability table.

    All providers succeed (200), so whichever target quality routing puts first is
    the one that actually serves — letting end-to-end tests assert on `reply.model`.
    """
    import json

    cap_file = tmp_path / "cap.json"
    cap_file.write_text(
        json.dumps({"scores": {k: {"score": v, "source": "arena"} for k, v in scores.items()}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("FREELLMPOOL_CAPABILITY_FILE", str(cap_file))
    _capability._table_cached.cache_clear()
    provider = Provider(
        id="x",
        label="X",
        adapter="openai",
        base_url="https://x.test/v1",
        key_env="X_KEY",
        models=tuple(models),
    )
    return Pool(
        [provider],
        quota=quota,
        env={"X_KEY": "k"},
        post=make_post({}),
        stream_post=make_stream_post({}),
        routing="quality",
    )


def test_fair_default_is_least_used(providers, env, quota):
    pool = Pool(providers, quota=quota, env=env, post=make_post({}))
    order = _names(pool)
    # nothing used yet → stable least-used ordering includes every enabled target
    assert "alpha/alpha-small" in order
    assert "beta/beta-1" in order


def test_fair_default_balances_by_provider_before_model(providers, env, quota):
    quota.record("alpha", "alpha-small", 1)
    pool = Pool(providers, quota=quota, env=env, post=make_post({}))
    order = _names(pool, include=["alpha", "beta"])

    assert order.index("beta/beta-1") < order.index("alpha/alpha-big")


def test_legacy_routing_balances_by_model(providers, env, quota):
    quota.record("alpha", "alpha-small", 1)
    pool = Pool(providers, quota=quota, env=env, post=make_post({}), routing="legacy")
    order = _names(pool, include=["alpha", "beta"])

    assert order.index("alpha/alpha-big") < order.index("beta/beta-1")


def test_fair_sinks_a_failing_target(providers, env, quota):
    pool = Pool(providers, quota=quota, env=env, post=make_post({}))
    # make beta look broken
    for _ in range(3):
        pool.metrics.record_failure("beta/beta-1", "down")
    order = _names(pool)
    assert order[-1] == "beta/beta-1", order  # failing target pushed to the back


def test_fast_mode_prefers_low_latency(providers, env, quota):
    pool = Pool(providers, quota=quota, env=env, post=make_post({}), routing="fast")
    pool.metrics.record_success("beta/beta-1", 50.0)
    pool.metrics.record_success("alpha/alpha-small", 900.0)
    order = _names(pool)
    assert order.index("beta/beta-1") < order.index("alpha/alpha-small")


def test_spread_serves_least_used_tier_first_over_faster_busy_provider(providers, env, quota):
    # The anti-429 property fast lacks: a heavily-used provider drops to a higher usage tier,
    # so spread serves the least-used one first EVEN IF the busy one is faster.
    from freellmpool.router import _SPREAD_BUCKET

    for _ in range(_SPREAD_BUCKET + 1):
        quota.record("beta", "beta-1")  # beta into a higher usage tier
    pool = Pool(providers, quota=quota, env=env, post=make_post({}), routing="spread")
    pool.metrics.record_success("beta/beta-1", 50.0)  # busy provider is FAST
    pool.metrics.record_success("alpha/alpha-small", 900.0)  # least-used is SLOW
    order = _names(pool, include=["alpha", "beta"])
    assert order.index("alpha/alpha-small") < order.index("beta/beta-1")


def test_spread_breaks_ties_by_latency_within_a_usage_tier(providers, env, quota):
    # Within the same usage tier (both unused), spread prefers the faster/healthier one —
    # the speed of fast, on top of the breadth of fair.
    pool = Pool(providers, quota=quota, env=env, post=make_post({}), routing="spread")
    pool.metrics.record_success("beta/beta-1", 50.0)
    pool.metrics.record_success("alpha/alpha-small", 900.0)
    order = _names(pool, include=["alpha", "beta"])
    assert order.index("beta/beta-1") < order.index("alpha/alpha-small")


def test_invalid_routing_falls_back_to_fair(providers, env, quota):
    pool = Pool(providers, quota=quota, env=env, post=make_post({}), routing="nonsense")
    assert pool.routing == "fair"


def test_chat_records_success_metric(providers, env, quota):
    pool = Pool(providers, quota=quota, env=env, post=make_post({}))
    reply = pool.chat([{"role": "user", "content": "hi"}])
    st = pool.metrics.get(f"{reply.provider_id}/{reply.model}")
    assert st is not None and st.ok == 1


def test_chat_records_failure_metric_on_bad_provider(providers, env, quota):
    # alpha 500s; the pool fails over but should record alpha's failure
    post = make_post({"alpha.test": (500, {"error": "boom"})})
    pool = Pool(providers, quota=quota, env=env, post=post)
    pool.chat([{"role": "user", "content": "hi"}], providers=["alpha", "beta"])
    assert pool.metrics.get("alpha/alpha-small").fail >= 1


def test_client_error_does_not_count_as_health_failure(providers, env, quota):
    # a 400 (bad request / capability) must NOT mark the provider failing — only
    # availability failures (429/5xx/network) do.
    post = make_post({"alpha.test": (400, {"error": "unsupported"})})
    pool = Pool(providers, quota=quota, env=env, post=post)
    pool.chat([{"role": "user", "content": "hi"}], providers=["alpha", "beta"])
    st = pool.metrics.get("alpha/alpha-small")
    assert st is None or st.fail == 0  # 400 didn't poison alpha's health


def test_402_capability_error_not_health_failure(providers, env, quota):
    post = make_post({"alpha.test": (402, {"error": "upgrade required"})})
    pool = Pool(providers, quota=quota, env=env, post=post)
    pool.chat([{"role": "user", "content": "hi"}], providers=["alpha", "beta"])
    st = pool.metrics.get("alpha/alpha-small")
    assert st is None or st.fail == 0


def test_account_quota_exhaustion_deprioritizes_provider_on_later_requests(
    providers, env, quota
):
    calls = []
    clock = {"now": 0.0}

    def post(url, headers, body, timeout):
        calls.append(url)
        if "alpha.test" in url:
            return HTTPResult(
                402,
                {"error": "You have depleted your monthly included credits."},
                "",
            )
        return HTTPResult(
            200,
            {"choices": [{"message": {"content": "ok"}}]},
            "",
        )

    pool = Pool(
        providers[:2],
        quota=quota,
        env=env,
        post=post,
        clock=lambda: clock["now"],
    )
    assert pool.chat(_EASY).provider_id == "beta"
    assert pool.cooldown_snapshot(clock["now"])["alpha"] >= 15 * 60
    calls.clear()

    assert pool.chat(_EASY).provider_id == "beta"
    assert "beta.test" in calls[0]

    clock["now"] += 15 * 60 + 1
    calls.clear()
    assert pool.chat(_EASY).provider_id == "beta"
    assert "alpha.test" in calls[0]


def test_quality_matches_difficulty_to_capability(tmp_path, monkeypatch, quota):
    pool = _quality_pool(
        tmp_path,
        monkeypatch,
        quota,
        scores={"big": 0.9, "small": 0.2},
        models=[Model("big"), Model("small")],
    )
    targets = pool._all_targets()
    # hard prompt → strong model first; easy prompt → light model first (rationing)
    assert pool._order(targets, difficulty=0.9)[0].model == "big"
    assert pool._order(targets, difficulty=0.1)[0].model == "small"


def test_agent_stays_in_strongest_capability_tier_and_spreads_usage(
    tmp_path, monkeypatch, quota
):
    from freellmpool.router import _SPREAD_BUCKET

    pool = _quality_pool(
        tmp_path,
        monkeypatch,
        quota,
        scores={"frontier-a": 0.99, "frontier-b": 0.96, "medium": 0.91, "weak": 0.5},
        models=[
            Model("frontier-a"),
            Model("frontier-b"),
            Model("medium"),
            Model("weak"),
        ],
    )
    targets = pool._all_targets()

    initial = [target.model for target in pool._order(targets, routing="agent")]
    assert initial[:2] == ["frontier-a", "frontier-b"]
    assert initial.index("frontier-b") < initial.index("medium")

    quota.record("x", "frontier-a", _SPREAD_BUCKET)
    spread = [target.model for target in pool._order(targets, routing="agent")]
    assert spread[:2] == ["frontier-b", "frontier-a"]
    assert spread.index("frontier-a") < spread.index("medium")


def test_agent_spreads_by_provider_not_catalog_width(monkeypatch, quota):
    """A provider must not earn extra traffic merely by listing more models."""
    monkeypatch.setattr("freellmpool.router.capability_table", lambda: {})
    monkeypatch.setattr("freellmpool.router.model_capability", lambda _name, _table: 0.99)
    wide = Provider(
        id="wide",
        label="Wide",
        adapter="openai",
        base_url="https://wide.test/v1",
        auth="none",
        models=(Model("wide-a"), Model("wide-b"), Model("wide-c")),
    )
    narrow = Provider(
        id="narrow",
        label="Narrow",
        adapter="openai",
        base_url="https://narrow.test/v1",
        auth="none",
        models=(Model("narrow-a"),),
    )
    for model in wide.models:
        quota.record("wide", model.name, 7)
    quota.record("narrow", "narrow-a", 7)

    pool = Pool([wide, narrow], quota=quota, env={})
    order = pool._order(pool._all_targets(), routing="agent")

    assert order[0].provider.id == "narrow"


def test_quality_over_budget_model_sinks(tmp_path, monkeypatch, quota):
    pool = _quality_pool(
        tmp_path,
        monkeypatch,
        quota,
        scores={"big": 0.9, "small": 0.2},
        models=[Model("big", rpd=1), Model("small")],
    )
    quota.record("x", "big", 1)  # big is now over its daily cap
    # even for a hard prompt, an over-budget strong model sinks behind a usable one
    order = [t.model for t in pool._order(pool._all_targets(), difficulty=0.9)]
    assert order[0] == "small"
    assert order[-1] == "big"  # still reachable, just last


def test_quality_latency_breaks_capability_near_tie(tmp_path, monkeypatch, quota):
    # Both models clear a hard prompt's bar. "slowbig" is the closest capability fit
    # (it would win on capability alone) but is painfully slow; "fastbig" is snappy.
    # Latency-aware quality must avoid the slow giant.
    pool = _quality_pool(
        tmp_path,
        monkeypatch,
        quota,
        scores={"slowbig": 0.90, "fastbig": 0.95},
        models=[Model("slowbig"), Model("fastbig")],
    )
    pool.metrics.record_success("x/slowbig", 30000.0)  # 30s
    pool.metrics.record_success("x/fastbig", 700.0)  # 0.7s
    order = [t.model for t in pool._order(pool._all_targets(), difficulty=0.90)]
    assert order[0] == "fastbig"  # capability-fit alone would put slowbig first


def test_quality_latency_never_overrides_capability_bar(tmp_path, monkeypatch, quota):
    # A fast but under-powered model must NOT leapfrog a capable one on a hard prompt:
    # the latency term is bounded below the under-power penalty.
    pool = _quality_pool(
        tmp_path,
        monkeypatch,
        quota,
        scores={"weakfast": 0.30, "strongslow": 0.95},
        models=[Model("weakfast"), Model("strongslow")],
    )
    pool.metrics.record_success("x/weakfast", 200.0)  # blazing
    pool.metrics.record_success("x/strongslow", 30000.0)  # slow
    order = [t.model for t in pool._order(pool._all_targets(), difficulty=0.90)]
    assert order[0] == "strongslow"  # hard prompt still gets the capable model


def test_quality_failing_model_sinks(tmp_path, monkeypatch, quota):
    pool = _quality_pool(
        tmp_path,
        monkeypatch,
        quota,
        scores={"big": 0.9, "small": 0.2},
        models=[Model("big"), Model("small")],
    )
    for _ in range(3):  # enough samples to mark "big" as failing
        pool.metrics.record_failure("x/big", "boom")
    order = [t.model for t in pool._order(pool._all_targets(), difficulty=0.9)]
    assert order[0] == "small"  # a healthy light model beats a failing strong one


# ---- end-to-end: difficulty is computed and threaded through the public APIs ----


def _qpool(tmp_path, monkeypatch, quota):
    # Both capabilities sit ABOVE the easy-prompt difficulty floor (~0.35), so the
    # easy/hard split exercises rationing (prefer the right-sized model) rather than
    # the under-powered penalty. small (0.5) wins easy; big (0.95) wins hard.
    return _quality_pool(
        tmp_path,
        monkeypatch,
        quota,
        scores={"big": 0.95, "small": 0.5},
        models=[Model("big"), Model("small")],
    )


def test_quality_chat_end_to_end(tmp_path, monkeypatch, quota):
    # All providers return 200, so the model that actually serves is the one quality
    # routing ordered first — proving difficulty is computed and threaded in chat().
    pool = _qpool(tmp_path, monkeypatch, quota)
    assert pool.chat(_EASY).model == "small"
    assert pool.chat(_HARD).model == "big"


def test_quality_stream_chat_end_to_end(tmp_path, monkeypatch, quota):
    pool = _qpool(tmp_path, monkeypatch, quota)

    def served(messages):
        meta = next(pool.stream_chat(messages))  # first yield is {"provider","model"}
        return meta["model"]

    assert served(_EASY) == "small"
    assert served(_HARD) == "big"


def test_quality_achat_end_to_end(tmp_path, monkeypatch, quota):
    import asyncio

    from freellmpool.aio import AsyncPool

    pool = _qpool(tmp_path, monkeypatch, quota)

    async def apost(url, headers, body, timeout):
        return pool._post(url, headers, body, timeout)

    apool = AsyncPool(pool, apost=apost)
    assert asyncio.run(apool.achat(_EASY)).model == "small"
    assert asyncio.run(apool.achat(_HARD)).model == "big"


# ---- per-request routing override (thread-safe; does not mutate self.routing) ----


def test_order_routing_override_beats_default(tmp_path, monkeypatch, quota):
    """A pool whose default is *not* quality still honors routing='quality' per call."""
    pool = _quality_pool(
        tmp_path,
        monkeypatch,
        quota,
        scores={"big": 0.9, "small": 0.2},
        models=[Model("big"), Model("small")],
    )
    pool.routing = "fair"  # flip default away from quality
    targets = pool._all_targets()
    # the per-call override reorders by capability even though the default is fair
    assert pool._order(targets, difficulty=0.9, routing="quality")[0].model == "big"
    # and it never mutates the pool's default
    assert pool.routing == "fair"


def test_order_invalid_routing_override_falls_back_to_default(tmp_path, monkeypatch, quota):
    pool = _quality_pool(
        tmp_path,
        monkeypatch,
        quota,
        scores={"big": 0.9, "small": 0.2},
        models=[Model("big"), Model("small")],
    )
    pool.routing = "fair"
    targets = pool._all_targets()
    # a bogus override is ignored → identical to the pool default ordering
    assert [t.model for t in pool._order(targets, routing="bogus")] == [
        t.model for t in pool._order(targets)
    ]


def test_chat_routing_override_end_to_end(tmp_path, monkeypatch, quota):
    pool = _qpool(tmp_path, monkeypatch, quota)
    pool.routing = "fast"  # default no longer computes difficulty
    # a per-call routing="quality" still sends the hard prompt to the strong model
    assert pool.chat(_HARD, routing="quality").model == "big"
    assert pool.routing == "fast"  # default untouched


def test_achat_routing_override_end_to_end(tmp_path, monkeypatch, quota):
    import asyncio

    from freellmpool.aio import AsyncPool

    pool = _qpool(tmp_path, monkeypatch, quota)
    pool.routing = "fast"

    async def apost(url, headers, body, timeout):
        return pool._post(url, headers, body, timeout)

    apool = AsyncPool(pool, apost=apost)
    assert asyncio.run(apool.achat(_HARD, routing="quality")).model == "big"
    assert pool.routing == "fast"
