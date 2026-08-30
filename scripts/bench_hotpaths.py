#!/usr/bin/env python3
"""Local hot-path benchmarks for routing/cache/quota/proxy-free operations.

This intentionally avoids network and benchmark dependencies. It prints JSON so
nightly/manual CI can retain comparable numbers without failing normal PRs over
machine variance.
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

from freellmpool.cache import Cache  # noqa: E402
from freellmpool.client import HTTPResult  # noqa: E402
from freellmpool.models import Model, Provider  # noqa: E402
from freellmpool.quota import QuotaStore  # noqa: E402
from freellmpool.route_health import RouteHealthStore  # noqa: E402
from freellmpool.router import Pool  # noqa: E402
from freellmpool.stats import StatsStore  # noqa: E402


def _providers(n_providers: int = 32, n_models: int = 12) -> list[Provider]:
    return [
        Provider(
            id=f"p{i}",
            label=f"P{i}",
            adapter="openai",
            base_url=f"https://p{i}.test/v1",
            auth="none",
            models=tuple(Model(f"m{j}", rpd=1000) for j in range(n_models)),
        )
        for i in range(n_providers)
    ]


def _post(url, headers, body, timeout):
    return HTTPResult(
        200,
        {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
        "ok",
    )


def _time(fn, iterations: int) -> dict:
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return {
        "iterations": iterations,
        "mean_ms": round(statistics.mean(samples), 4),
        "median_ms": round(statistics.median(samples), 4),
        "p95_ms": round(sorted(samples)[int(iterations * 0.95) - 1], 4),
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        providers = _providers()
        quota = QuotaStore(
            path=root / "quota.json", flush_every=32, flush_interval=60
        )
        stats = StatsStore(
            root / "stats.json", flush_every=32, flush_interval=60
        )
        health = RouteHealthStore(
            path=root / "health.json",
            success_flush_every=32,
            success_flush_interval=60,
        )
        cache = Cache(ttl=3600, path=root / "cache.db", max_entries=1000)
        pool = Pool(
            providers,
            quota=quota,
            env={},
            post=_post,
            cache=cache,
            routing="spread",
            stats_store=stats,
            route_health=health,
        )
        messages = [{"role": "user", "content": "hello"}]

        uncached_quota = QuotaStore(
            path=root / "uncached-quota.json",
            flush_every=32,
            flush_interval=60,
        )
        uncached_stats = StatsStore(
            root / "uncached-stats.json",
            flush_every=32,
            flush_interval=60,
        )
        uncached_health = RouteHealthStore(
            path=root / "uncached-health.json",
            success_flush_every=32,
            success_flush_interval=60,
        )
        uncached_pool = Pool(
            providers[:4],
            quota=uncached_quota,
            env={},
            post=_post,
            routing="spread",
            stats_store=uncached_stats,
            route_health=uncached_health,
        )

        route_health = RouteHealthStore(
            path=root / "route-cycle-health.json",
            success_flush_every=32,
            success_flush_interval=60,
        )
        route_keys = ("p0/*", "p0/m0")

        def route_acquire_success() -> None:
            lease = route_health.acquire_many(route_keys)
            if lease is None:
                raise RuntimeError("benchmark route unexpectedly unavailable")
            route_health.record_success_many(
                tuple(reversed(route_keys)), 10.0, lease=lease
            )

        def construct_real_pool() -> Pool:
            return Pool(
                providers[:4],
                quota=QuotaStore(
                    path=root / "construct-quota.json",
                    flush_every=32,
                    flush_interval=60,
                ),
                env={},
                post=_post,
                stats_store=StatsStore(
                    root / "construct-stats.json",
                    flush_every=32,
                    flush_interval=60,
                ),
                route_health=RouteHealthStore(
                    path=root / "construct-health.json",
                    success_flush_every=32,
                    success_flush_interval=60,
                ),
            )

        flush_counter = 0

        def record_and_flush_real_stores() -> None:
            nonlocal flush_counter
            flush_counter += 1
            quota.record("p0", "m0")
            stats.add(requests=1, prompt_tokens=3, completion_tokens=2)
            health.record_success("p0/m0", float(flush_counter))
            pool.flush()

        route_cycle_result = _time(route_acquire_success, 100)
        route_health.flush()
        health_snapshot_result = _time(route_health.snapshot, 100)

        results = {
            "rank_targets_large_catalog": _time(lambda: pool.rank_targets(messages), 1000),
            "chat_cache_hit": _time(lambda: pool.chat(messages), 500),
            "quota_batched_record": _time(lambda: quota.record("p0", "m0"), 2000),
            "pool_construction_real_stores": _time(construct_real_pool, 100),
            "chat_uncached_real_accounting": _time(
                lambda: uncached_pool.chat(
                    messages, model="m0", providers=["p0"]
                ),
                100,
            ),
            "route_acquire_success": route_cycle_result,
            "health_snapshot": health_snapshot_result,
            "telemetry_record_and_explicit_flush": _time(
                record_and_flush_real_stores, 25
            ),
            "cache_get_put": _time(
                lambda: (cache.put("bench", {"text": "ok"}), cache.get("bench")),
                1000,
            ),
        }
        for benchmark_pool in (pool, uncached_pool):
            benchmark_pool.flush()
        route_health.flush()
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
