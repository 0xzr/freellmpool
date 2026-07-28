"""QuotaStore persistence + UTC-day reset."""

from __future__ import annotations

from datetime import UTC, datetime

from freellmpool.quota import QuotaStore


def _store(tmp_path, day):
    clock = lambda: datetime(2026, 6, day, 12, 0, tzinfo=UTC)  # noqa: E731
    return QuotaStore(path=tmp_path / "q.json", clock=clock)


def test_record_and_used(tmp_path):
    s = _store(tmp_path, 2)
    assert s.used("groq", "m") == 0
    assert s.record("groq", "m") == 1
    assert s.record("groq", "m") == 2
    assert s.used("groq", "m") == 2


def test_persists_across_instances(tmp_path):
    _store(tmp_path, 2).record("groq", "m", 4)
    assert _store(tmp_path, 2).used("groq", "m") == 4


def test_resets_at_utc_midnight(tmp_path):
    _store(tmp_path, 2).record("groq", "m", 7)
    fresh = _store(tmp_path, 3)  # next UTC day
    assert fresh.used("groq", "m") == 0


def test_over_budget(tmp_path):
    s = _store(tmp_path, 2)
    s.record("groq", "m", 3)
    assert s.over_budget("groq", "m", rpd=3) is True
    assert s.over_budget("groq", "m", rpd=5) is False
    assert s.over_budget("groq", "m", rpd=0) is False  # 0 = unmetered hint


def test_snapshot(tmp_path):
    s = _store(tmp_path, 2)
    s.record("groq", "a", 2)
    s.record("cerebras", "b", 1)
    snap = s.snapshot()
    assert snap == {"groq::a": 2, "cerebras::b": 1}


def test_record_merges_concurrent_external_writes(tmp_path):
    # Two stores share the file (as the proxy + a CLI process would). An increment
    # from store B must not clobber an increment store A persisted in between —
    # record() reloads under a cross-process lock before writing.
    a = _store(tmp_path, 2)
    b = _store(tmp_path, 2)
    a.record("groq", "m", 1)  # A writes groq::m = 1
    b.record("cerebras", "n", 1)  # B records its own key — must preserve A's
    assert b.snapshot() == {"groq::m": 1, "cerebras::n": 1}
    # and A sees B's write after a reload
    assert a.snapshot() == {"groq::m": 1, "cerebras::n": 1}


def test_batched_record_flushes_on_threshold(tmp_path):
    s = QuotaStore(
        path=tmp_path / "q.json",
        clock=lambda: datetime(2026, 6, 2, 12, 0, tzinfo=UTC),
        flush_every=3,
    )
    assert s.record("groq", "m") == 1
    assert not (tmp_path / "q.json").exists()
    assert s.record("groq", "m") == 2
    assert not (tmp_path / "q.json").exists()
    assert s.record("groq", "m") == 3
    assert _store(tmp_path, 2).used("groq", "m") == 3


def test_batched_flush_merges_external_writes(tmp_path):
    a = QuotaStore(
        path=tmp_path / "q.json",
        clock=lambda: datetime(2026, 6, 2, 12, 0, tzinfo=UTC),
        flush_every=10,
    )
    b = _store(tmp_path, 2)
    a.record("groq", "m")
    b.record("cerebras", "n")
    a.flush()
    assert _store(tmp_path, 2).snapshot() == {"groq::m": 1, "cerebras::n": 1}


def test_malformed_quota_file_recovers_on_record(tmp_path):
    path = tmp_path / "q.json"
    path.write_text("{not valid json", encoding="utf-8")
    s = QuotaStore(path=path, clock=lambda: datetime(2026, 6, 2, 12, 0, tzinfo=UTC))
    assert s.record("groq", "m") == 1
    assert _store(tmp_path, 2).snapshot() == {"groq::m": 1}


def test_batched_flush_failure_keeps_pending_visible(tmp_path, monkeypatch):
    s = QuotaStore(
        path=tmp_path / "q.json",
        clock=lambda: datetime(2026, 6, 2, 12, 0, tzinfo=UTC),
        flush_every=10,
    )
    s.record("groq", "m", 2)
    with monkeypatch.context() as m:
        m.setattr(s, "_save", lambda: (_ for _ in ()).throw(OSError("disk full")))
        s.flush()  # best effort; must keep pending increments
        assert s.snapshot() == {"groq::m": 2}

    s.flush()
    assert _store(tmp_path, 2).snapshot() == {"groq::m": 2}


def test_batched_flush_persists_prior_day_pending_after_utc_midnight(tmp_path):
    path = tmp_path / "q.json"
    day2 = datetime(2026, 6, 2, 23, 59, tzinfo=UTC)
    day3 = datetime(2026, 6, 3, 0, 1, tzinfo=UTC)
    now = {"value": day2}
    store = QuotaStore(path=path, clock=lambda: now["value"], flush_every=10)

    store.record("groq", "m", 3)
    now["value"] = day3
    store.flush()

    assert QuotaStore(path=path, clock=lambda: day2).used("groq", "m") == 3
    assert QuotaStore(path=path, clock=lambda: day3).used("groq", "m") == 0
