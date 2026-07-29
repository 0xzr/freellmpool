"""Persistent per-route health and circuit-breaker behavior."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from freellmpool.aio import AsyncPool
from freellmpool.client import HTTPResult
from freellmpool.errors import AllProvidersExhausted
from freellmpool.models import Model, Provider
from freellmpool.proxy import _readiness_snapshot, _status_payload
from freellmpool.quota import QuotaStore
from freellmpool.route_health import FailureUpdate, RouteHealthStore
from freellmpool.router import Pool


def _provider() -> Provider:
    return Provider(
        id="alpha",
        label="Alpha",
        adapter="openai",
        base_url="https://alpha.test/v1",
        key_env="ALPHA_KEY",
        models=(Model("alpha-model"),),
    )


def _ok_result() -> HTTPResult:
    return HTTPResult(
        200,
        {"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        "",
    )


def test_restart_preserves_open_circuit_and_half_open_recovery(tmp_path):
    now = [1_000.0]
    path = tmp_path / "health.json"
    first = RouteHealthStore(
        path=path,
        clock=lambda: now[0],
        failure_threshold=1,
        base_cooldown=30.0,
    )
    first.record_failure("alpha/model", "availability")

    restarted = RouteHealthStore(
        path=path,
        clock=lambda: now[0],
        failure_threshold=1,
        base_cooldown=30.0,
    )
    assert restarted.state("alpha/model").state == "open"
    assert not restarted.allow("alpha/model")

    now[0] = 1_031.0
    assert restarted.allow("alpha/model")
    assert restarted.state("alpha/model").state == "half_open"
    # A second process cannot consume the same half-open probe lease.
    competing = RouteHealthStore(path=path, clock=lambda: now[0], failure_threshold=1)
    assert not competing.allow("alpha/model")

    restarted.record_success("alpha/model", 125.0)
    recovered = RouteHealthStore(path=path, clock=lambda: now[0])
    row = recovered.state("alpha/model")
    assert row.state == "closed"
    assert row.consecutive_failures == 0
    assert row.ewma_ms == 125.0
    assert recovered.allow("alpha/model")


def test_retry_after_opens_until_provider_reset(tmp_path):
    now = [50.0]
    store = RouteHealthStore(path=tmp_path / "health.json", clock=lambda: now[0])

    store.record_failure(
        "alpha/*",
        "rate_limit",
        retry_after=90.0,
        open_immediately=True,
    )

    row = store.state("alpha/*")
    assert row.open_until == 140.0
    assert not store.allow("alpha/*")
    now[0] = 141.0
    assert store.allow("alpha/*")


def test_client_and_capability_failures_do_not_poison_availability(tmp_path):
    store = RouteHealthStore(path=tmp_path / "health.json", failure_threshold=1)

    for failure_class in ("client", "capability", "auth", "retirement"):
        store.record_failure(
            f"alpha/{failure_class}",
            failure_class,
            counts_for_health=False,
        )
        row = store.state(f"alpha/{failure_class}")
        assert row.failure_class == failure_class
        assert row.consecutive_failures == 0
        assert row.state == "closed"
        assert store.allow(f"alpha/{failure_class}")


def test_non_availability_response_releases_half_open_probe(tmp_path):
    now = [100.0]
    store = RouteHealthStore(
        path=tmp_path / "health.json",
        clock=lambda: now[0],
        failure_threshold=1,
        base_cooldown=10,
    )
    store.record_failure("alpha/model", "availability")
    now[0] = 111.0
    assert store.allow("alpha/model")
    assert store.state("alpha/model").state == "half_open"

    store.record_failure("alpha/model", "client", counts_for_health=False)

    row = store.state("alpha/model")
    assert row.state == "closed"
    assert row.consecutive_failures == 0


def test_stale_and_corrupt_state_are_ignored(tmp_path):
    now = [1_000.0]
    path = tmp_path / "health.json"
    store = RouteHealthStore(
        path=path,
        clock=lambda: now[0],
        stale_after=10.0,
        failure_threshold=1,
    )
    store.record_failure("alpha/model", "availability")
    now[0] = 1_011.0
    assert store.snapshot() == {}

    path.write_text("{not-json", encoding="utf-8")
    assert store.snapshot() == {}
    store.record_success("beta/model", 10.0)
    assert set(json.loads(path.read_text(encoding="utf-8"))["routes"]) == {"beta/model"}

    path.write_bytes(b"x" * 2_000_001)
    assert store.snapshot() == {}

    path.write_text(
        json.dumps(
            {
                "version": 1,
                "routes": {
                    "crafted/model": {
                        "state": [],
                        "failure_class": {},
                        "updated_at": now[0],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    row = store.state("crafted/model")
    assert row.state == "closed"
    assert row.failure_class is None

    path.write_text(
        '{"version":1,"routes":{"crafted/model":{"updated_at":'
        + "9" * 4_000
        + "}}}",
        encoding="utf-8",
    )
    assert store.snapshot() == {}


def test_hostile_maximum_length_integer_cannot_break_health_update(tmp_path):
    path = tmp_path / "health.json"
    path.write_text(
        '{"version":1,"routes":{"crafted/model":{"state":"closed",'
        '"updated_at":100,"lease_generation":'
        + "9" * 4_300
        + "}}}",
        encoding="utf-8",
    )
    store = RouteHealthStore(path=path, clock=lambda: 100.0)

    older = store.acquire_many(("crafted/model",))
    newer = store.acquire_many(("crafted/model",))

    assert older is not None
    assert newer is not None
    assert older.generations["crafted/model"] < newer.generations["crafted/model"]

    store.record_success("crafted/model", 10.0, lease=newer)
    store.record_failure(
        "crafted/model",
        "availability",
        lease=older,
    )
    row = store.state("crafted/model")
    assert row.state == "closed"
    assert row.consecutive_failures == 0


def test_state_is_size_bounded_and_contains_only_sanitized_fields(tmp_path):
    now = [100.0]
    path = tmp_path / "health.json"
    store = RouteHealthStore(path=path, clock=lambda: now[0], max_entries=3)
    for index in range(5):
        now[0] += 1
        store.record_failure(f"provider/model-{index}", "availability")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["routes"]) == 3
    serialized = json.dumps(payload)
    assert "prompt" not in serialized
    assert "secret" not in serialized
    assert set(next(iter(payload["routes"].values()))) <= {
        "state",
        "successes",
        "failures",
        "consecutive_failures",
        "ewma_ms",
        "last_success",
        "last_failure",
        "failure_class",
        "open_until",
        "half_open_until",
        "lease_generation",
        "open_count",
        "updated_at",
    }

    payload["routes"].update(
        {
            f"manual/model-{index}": {
                "state": "closed",
                "updated_at": now[0] + index + 1,
            }
            for index in range(5)
        }
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert len(store.snapshot()) == 3


def test_concurrent_instances_do_not_lose_updates(tmp_path):
    path = tmp_path / "health.json"

    def record(index: int) -> None:
        RouteHealthStore(path=path).record_success("alpha/model", float(index + 1))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(record, range(16)))

    row = RouteHealthStore(path=path).state("alpha/model")
    assert row.successes == 16
    assert row.failures == 0


def test_route_and_provider_results_commit_in_one_transaction(tmp_path):
    class CountingStore(RouteHealthStore):
        writes = 0

        def _write(self, routes):
            self.writes += 1
            return super()._write(routes)

    store = CountingStore(path=tmp_path / "health.json")
    keys = ("alpha/model", "alpha/*")
    lease = store.acquire_many(keys)

    store.writes = 0
    store.record_success_many(keys, 10.0, lease=lease)
    assert store.writes == 1
    assert all(store.state(key).successes == 1 for key in keys)

    lease = store.acquire_many(keys)
    store.writes = 0
    store.record_failures(
        (
            FailureUpdate("alpha/model", "rate_limit", 60, True, True),
            FailureUpdate("alpha/*", "rate_limit", 60, True, True),
        ),
        lease=lease,
    )
    assert store.writes == 1
    assert all(store.state(key).state == "open" for key in keys)


def test_stale_half_open_result_cannot_overwrite_newer_probe(tmp_path):
    now = [1_000.0]
    store = RouteHealthStore(
        path=tmp_path / "health.json",
        clock=lambda: now[0],
        failure_threshold=1,
        base_cooldown=30,
        half_open_lease=10,
    )
    store.record_failure("alpha/model", "availability")

    now[0] = 1_031.0
    first_probe = store.acquire_many(("alpha/model",))
    assert first_probe is not None

    now[0] = 1_042.0
    second_probe = store.acquire_many(("alpha/model",))
    assert second_probe is not None
    store.record_failure(
        "alpha/model",
        "availability",
        lease=second_probe,
    )
    assert store.state("alpha/model").state == "open"

    now[0] = 1_043.0
    store.record_success("alpha/model", 10.0, lease=first_probe)
    assert store.state("alpha/model").state == "open"


def test_older_failure_cannot_reopen_after_newer_request_succeeds(tmp_path):
    now = [100.0]
    store = RouteHealthStore(
        path=tmp_path / "health.json",
        clock=lambda: now[0],
        failure_threshold=1,
    )
    older = store.acquire_many(("alpha/model",))
    now[0] = 101.0
    newer = store.acquire_many(("alpha/model",))

    now[0] = 102.0
    store.record_success("alpha/model", 12.0, lease=newer)
    now[0] = 103.0
    store.record_failure("alpha/model", "availability", lease=older)

    row = store.state("alpha/model")
    assert row.state == "closed"
    assert row.consecutive_failures == 0
    assert row.successes == 1
    assert row.failures == 1


def test_same_timestamp_closed_leases_have_distinct_generations(tmp_path):
    now = [100.0]
    path = tmp_path / "health.json"
    older_store = RouteHealthStore(
        path=path,
        clock=lambda: now[0],
        failure_threshold=1,
    )
    newer_store = RouteHealthStore(
        path=path,
        clock=lambda: now[0],
        failure_threshold=1,
    )
    older = older_store.acquire_many(("alpha/model",))
    newer = newer_store.acquire_many(("alpha/model",))
    assert older.generations["alpha/model"] < newer.generations["alpha/model"]

    newer_store.record_success("alpha/model", 10.0, lease=newer)
    older_store.record_failure(
        "alpha/model",
        "availability",
        lease=older,
    )

    row = RouteHealthStore(path=path, clock=lambda: now[0]).state("alpha/model")
    assert row.state == "closed"
    assert row.consecutive_failures == 0


def test_missing_fchmod_and_non_fcntl_instances_remain_safe(
    tmp_path, monkeypatch
):
    import freellmpool.route_health as route_health

    path = tmp_path / "health.json"
    monkeypatch.delattr(route_health.os, "fchmod")
    monkeypatch.setattr(route_health, "fcntl", None)

    def record(index: int) -> None:
        RouteHealthStore(path=path).record_success("alpha/model", index)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(record, range(8)))

    assert RouteHealthStore(path=path).state("alpha/model").successes == 8


def test_telemetry_filesystem_failure_cannot_break_successful_request(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("file", encoding="utf-8")
    store = RouteHealthStore(path=blocker / "health.json")
    pool = Pool(
        [_provider()],
        env={"ALPHA_KEY": "a"},
        post=lambda *args: _ok_result(),
        route_health=store,
        quota=QuotaStore(path=tmp_path / "quota.json"),
    )

    assert pool.ask("hello").text == "ok"
    assert store.state("alpha/alpha-model").successes == 1


def test_pool_restart_skips_open_route_then_runs_one_half_open_probe(tmp_path):
    now = [1_000.0]
    path = tmp_path / "health.json"
    calls: list[str] = []

    def unavailable(url, headers, body, timeout):
        calls.append(url)
        return HTTPResult(503, {"error": {"message": "down"}}, "")

    health = RouteHealthStore(
        path=path,
        clock=lambda: now[0],
        failure_threshold=1,
        base_cooldown=30,
    )
    first = Pool(
        [_provider()],
        env={"ALPHA_KEY": "a"},
        post=unavailable,
        route_health=health,
    )
    with pytest.raises(AllProvidersExhausted):
        first.ask("hello")
    assert len(calls) == 1

    def available(url, headers, body, timeout):
        calls.append(url)
        return _ok_result()

    restarted = Pool(
        [_provider()],
        env={"ALPHA_KEY": "a"},
        post=available,
        route_health=RouteHealthStore(
            path=path,
            clock=lambda: now[0],
            failure_threshold=1,
            base_cooldown=30,
        ),
    )
    with pytest.raises(AllProvidersExhausted):
        restarted.ask("still open")
    assert len(calls) == 1

    now[0] = 1_031.0
    assert restarted.ask("probe").text == "ok"
    assert len(calls) == 2
    assert restarted.route_health.state("alpha/alpha-model").state == "closed"


def test_pool_uses_retry_after_for_provider_wide_rate_limit(tmp_path):
    now = [100.0]
    health = RouteHealthStore(path=tmp_path / "health.json", clock=lambda: now[0])

    def rate_limited(url, headers, body, timeout):
        return HTTPResult(
            429,
            {"error": {"message": "slow down"}},
            "",
            headers={"Retry-After": "75"},
        )

    pool = Pool(
        [_provider()],
        env={"ALPHA_KEY": "a"},
        post=rate_limited,
        route_health=health,
    )
    with pytest.raises(AllProvidersExhausted):
        pool.ask("hello")

    row = health.state("alpha/*")
    assert row.failure_class == "rate_limit"
    assert row.open_until == 175.0
    assert not health.allow("alpha/*")


def test_provider_quota_never_shortens_longer_retry_after(tmp_path):
    now = [100.0]
    health = RouteHealthStore(path=tmp_path / "health.json", clock=lambda: now[0])

    def quota_exhausted(url, headers, body, timeout):
        return HTTPResult(
            429,
            {"error": {"message": "quota exhausted"}},
            "",
            headers={"Retry-After": "3600"},
        )

    pool = Pool(
        [_provider()],
        env={"ALPHA_KEY": "a"},
        post=quota_exhausted,
        route_health=health,
    )
    with pytest.raises(AllProvidersExhausted):
        pool.ask("hello")

    assert health.state("alpha/*").open_until == 3_700.0


def test_pool_records_client_error_without_opening_route(tmp_path):
    health = RouteHealthStore(
        path=tmp_path / "health.json",
        failure_threshold=1,
    )

    def bad_request(url, headers, body, timeout):
        return HTTPResult(400, {"error": {"message": "bad input"}}, "")

    pool = Pool(
        [_provider()],
        env={"ALPHA_KEY": "a"},
        post=bad_request,
        route_health=health,
    )
    with pytest.raises(AllProvidersExhausted):
        pool.ask("hello")

    row = health.state("alpha/alpha-model")
    assert row.failure_class == "client"
    assert row.consecutive_failures == 0
    assert row.state == "closed"
    assert health.allow("alpha/alpha-model")


def test_streaming_failures_and_recovery_use_persistent_circuit(tmp_path):
    now = [10.0]
    path = tmp_path / "health.json"
    calls: list[str] = []

    def unavailable(url, headers, body, timeout):
        calls.append(url)
        return 503, iter(())

    health = RouteHealthStore(
        path=path,
        clock=lambda: now[0],
        failure_threshold=1,
        base_cooldown=20,
    )
    pool = Pool(
        [_provider()],
        env={"ALPHA_KEY": "a"},
        stream_post=unavailable,
        route_health=health,
    )
    with pytest.raises(AllProvidersExhausted):
        list(pool.stream_chat([{"role": "user", "content": "hello"}]))

    def available(url, headers, body, timeout):
        calls.append(url)
        return 200, iter(
            (
                'data: {"choices":[{"delta":{"content":"ok"}}]}',
                "data: [DONE]",
            )
        )

    restarted = Pool(
        [_provider()],
        env={"ALPHA_KEY": "a"},
        stream_post=available,
        route_health=RouteHealthStore(
            path=path,
            clock=lambda: now[0],
            failure_threshold=1,
            base_cooldown=20,
        ),
    )
    with pytest.raises(AllProvidersExhausted):
        list(restarted.stream_chat([{"role": "user", "content": "open"}]))
    assert len(calls) == 1

    now[0] = 31.0
    chunks = list(restarted.stream_chat([{"role": "user", "content": "probe"}]))
    assert chunks[-1] == "ok"
    assert len(calls) == 2
    assert restarted.route_health.state("alpha/alpha-model").state == "closed"


def test_midstream_reset_records_failure_instead_of_persistent_success(tmp_path):
    health = RouteHealthStore(
        path=tmp_path / "health.json",
        failure_threshold=1,
    )

    def chunks():
        yield 'data: {"choices":[{"delta":{"content":"partial"}}]}'
        raise ConnectionError("reset")

    pool = Pool(
        [_provider()],
        env={"ALPHA_KEY": "a"},
        stream_post=lambda *args: (200, iter(chunks())),
        route_health=health,
        quota=QuotaStore(path=tmp_path / "quota.json"),
    )

    with pytest.raises(ConnectionError, match="reset"):
        list(pool.stream_chat([{"role": "user", "content": "hello"}]))

    row = health.state("alpha/alpha-model")
    assert row.state == "open"
    assert row.failure_class == "transport"
    assert row.successes == 0
    assert row.failures == 1


def test_streaming_rate_limit_persists_provider_reset_header(tmp_path):
    now = [300.0]
    health = RouteHealthStore(
        path=tmp_path / "health.json",
        clock=lambda: now[0],
    )
    pool = Pool(
        [_provider()],
        env={"ALPHA_KEY": "a"},
        stream_post=lambda *args: (
            429,
            {"RateLimit-Reset": "65"},
            iter(("rate limited",)),
        ),
        route_health=health,
        quota=QuotaStore(path=tmp_path / "quota.json"),
    )

    with pytest.raises(AllProvidersExhausted):
        list(pool.stream_chat([{"role": "user", "content": "hello"}]))

    row = health.state("alpha/*")
    assert row.failure_class == "rate_limit"
    assert row.open_until == 365.0


def test_async_failover_uses_persistent_circuit(tmp_path):
    now = [20.0]
    path = tmp_path / "health.json"
    calls: list[str] = []

    async def unavailable(url, headers, body, timeout):
        calls.append(url)
        return HTTPResult(503, {"error": {"message": "down"}}, "")

    health = RouteHealthStore(
        path=path,
        clock=lambda: now[0],
        failure_threshold=1,
        base_cooldown=15,
    )
    first = AsyncPool(
        Pool(
            [_provider()],
            env={"ALPHA_KEY": "a"},
            route_health=health,
        ),
        apost=unavailable,
    )
    with pytest.raises(AllProvidersExhausted):
        asyncio.run(first.aask("hello"))

    async def available(url, headers, body, timeout):
        calls.append(url)
        return _ok_result()

    restarted_pool = Pool(
        [_provider()],
        env={"ALPHA_KEY": "a"},
        route_health=RouteHealthStore(
            path=path,
            clock=lambda: now[0],
            failure_threshold=1,
            base_cooldown=15,
        ),
    )
    restarted = AsyncPool(restarted_pool, apost=available)
    with pytest.raises(AllProvidersExhausted):
        asyncio.run(restarted.aask("open"))
    assert len(calls) == 1

    now[0] = 36.0
    assert asyncio.run(restarted.aask("probe")).text == "ok"
    assert len(calls) == 2
    assert restarted_pool.route_health.state("alpha/alpha-model").state == "closed"


def test_default_pool_wires_configurable_persistent_health_path(tmp_path):
    path = tmp_path / "custom-health.json"
    pool = Pool.from_default_config(
        env={
            "FREELLMPOOL_HEALTH_FILE": str(path),
            "FREELLMPOOL_CONFIG": str(tmp_path / "missing.toml"),
        }
    )

    assert pool.route_health.path == path


def test_embedding_routes_respect_persistent_circuit(tmp_path):
    path = tmp_path / "health.json"
    calls: list[str] = []
    health = RouteHealthStore(path=path, failure_threshold=1, base_cooldown=60)

    def unavailable(url, headers, body, timeout):
        calls.append(url)
        return HTTPResult(503, {"error": {"message": "down"}}, "")

    first = Pool(
        [],
        embedders=[_provider()],
        env={"ALPHA_KEY": "a"},
        post=unavailable,
        route_health=health,
    )
    with pytest.raises(AllProvidersExhausted):
        first.embed("hello")

    def available(url, headers, body, timeout):
        calls.append(url)
        return HTTPResult(200, {"data": [{"embedding": [1.0, 2.0]}]}, "")

    restarted = Pool(
        [],
        embedders=[_provider()],
        env={"ALPHA_KEY": "a"},
        post=available,
        route_health=RouteHealthStore(
            path=path,
            failure_threshold=1,
            base_cooldown=60,
        ),
    )
    with pytest.raises(AllProvidersExhausted):
        restarted.embed("still open")
    assert len(calls) == 1


def test_transcription_routes_respect_persistent_circuit(tmp_path):
    path = tmp_path / "health.json"
    calls: list[str] = []
    health = RouteHealthStore(path=path, failure_threshold=1, base_cooldown=60)

    def unavailable(url, headers, files, data, timeout):
        calls.append(url)
        return HTTPResult(503, {"error": {"message": "down"}}, "")

    first = Pool(
        [],
        transcribers=[_provider()],
        env={"ALPHA_KEY": "a"},
        transcribe_post=unavailable,
        route_health=health,
    )
    with pytest.raises(AllProvidersExhausted):
        first.transcribe(b"audio", "clip.wav")

    def available(url, headers, files, data, timeout):
        calls.append(url)
        return HTTPResult(200, {"text": "ok"}, "")

    restarted = Pool(
        [],
        transcribers=[_provider()],
        env={"ALPHA_KEY": "a"},
        transcribe_post=available,
        route_health=RouteHealthStore(
            path=path,
            failure_threshold=1,
            base_cooldown=60,
        ),
    )
    with pytest.raises(AllProvidersExhausted):
        restarted.transcribe(b"audio", "clip.wav")
    assert len(calls) == 1


def test_sample_age_and_provider_cooldown_are_derived_from_store_clock(tmp_path):
    now = [500.0]
    store = RouteHealthStore(path=tmp_path / "health.json", clock=lambda: now[0])
    store.record_failure(
        "alpha/*",
        "rate_limit",
        retry_after=90,
        open_immediately=True,
    )

    row = store.state("alpha/*")
    assert store.sample_age(row) == 0.0
    assert store.provider_cooldowns() == {"alpha": 90.0}

    now[0] = 505.0
    assert store.sample_age(row) == 5.0
    assert store.provider_cooldowns() == {"alpha": 85.0}


def test_status_exposes_persistent_route_health_without_raw_errors(tmp_path):
    now = [800.0]
    store = RouteHealthStore(
        path=tmp_path / "health.json",
        clock=lambda: now[0],
        failure_threshold=1,
    )
    store.record_failure("alpha/alpha-model", "availability")
    pool = Pool(
        [_provider()],
        env={"ALPHA_KEY": "a"},
        route_health=store,
        quota=QuotaStore(path=tmp_path / "quota.json"),
    )

    payload = _status_payload(pool, [])
    model = payload["providers"][0]["models"][0]
    assert model["circuit_state"] == "open"
    assert model["failure_class"] == "availability"
    assert model["consecutive_failures"] == 1
    assert model["sample_age_s"] == 0.0
    assert model["last_error"] is None


def test_readiness_marks_persistently_open_model_unavailable(tmp_path):
    store = RouteHealthStore(
        path=tmp_path / "health.json",
        failure_threshold=1,
    )
    store.record_failure("alpha/alpha-model", "availability")
    pool = Pool(
        [_provider()],
        env={"ALPHA_KEY": "a"},
        route_health=store,
        quota=QuotaStore(path=tmp_path / "quota.json"),
    )

    provider = _readiness_snapshot(pool).providers[0]
    assert provider.ready is False
    assert provider.status == "cooldown"
    assert provider.models[0].status == "cooldown"


def test_status_includes_embedding_and_transcription_circuits(tmp_path):
    store = RouteHealthStore(
        path=tmp_path / "health.json",
        failure_threshold=1,
    )
    store.record_failure("alpha/alpha-model", "availability")
    pool = Pool(
        [],
        embedders=[_provider()],
        transcribers=[_provider()],
        env={"ALPHA_KEY": "a"},
        route_health=store,
        quota=QuotaStore(path=tmp_path / "quota.json"),
    )

    routes = _status_payload(pool, [])["routes"]
    assert routes["embeddings"][0]["circuit_state"] == "open"
    assert routes["transcriptions"][0]["failure_class"] == "availability"


def test_quality_routing_uses_persisted_latency_after_restart(tmp_path):
    shared = Model("shared-model")
    slow = Provider(
        id="slow",
        label="Slow",
        adapter="openai",
        base_url="https://slow.test/v1",
        key_env="SLOW_KEY",
        models=(shared,),
    )
    fast = Provider(
        id="fast",
        label="Fast",
        adapter="openai",
        base_url="https://fast.test/v1",
        key_env="FAST_KEY",
        models=(shared,),
    )
    store = RouteHealthStore(path=tmp_path / "health.json")
    store.record_success("slow/shared-model", 8_000)
    store.record_success("fast/shared-model", 100)
    pool = Pool(
        [slow, fast],
        env={"SLOW_KEY": "s", "FAST_KEY": "f"},
        route_health=RouteHealthStore(path=tmp_path / "health.json"),
        quota=QuotaStore(path=tmp_path / "quota.json"),
        routing="quality",
    )

    ranked = pool.rank_targets([{"role": "user", "content": "explain this"}])
    assert ranked[0].provider.id == "fast"


def test_async_ordering_keeps_persistent_disk_reads_off_event_loop(
    tmp_path, monkeypatch
):
    pool = Pool(
        [_provider()],
        env={"ALPHA_KEY": "a"},
        route_health=RouteHealthStore(path=tmp_path / "health.json"),
        quota=QuotaStore(path=tmp_path / "quota.json"),
    )
    loop_thread = threading.get_ident()
    seen: list[int] = []
    original = pool._order

    def observed_order(*args, **kwargs):
        seen.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(pool, "_order", observed_order)

    async def post(url, headers, body, timeout):
        return _ok_result()

    assert asyncio.run(AsyncPool(pool, apost=post).aask("hello")).text == "ok"
    assert seen and seen[0] != loop_thread
