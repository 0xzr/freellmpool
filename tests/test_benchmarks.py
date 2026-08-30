"""Local benchmark comparison helpers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "compare_benchmarks.py"
_SPEC = importlib.util.spec_from_file_location("compare_benchmarks", _SCRIPT_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
compare = _MODULE.compare

_HOTPATH_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_hotpaths.py"
_HOTPATH_SPEC = importlib.util.spec_from_file_location("bench_hotpaths", _HOTPATH_SCRIPT)
assert _HOTPATH_SPEC is not None
assert _HOTPATH_SPEC.loader is not None
_HOTPATH_MODULE = importlib.util.module_from_spec(_HOTPATH_SPEC)
_HOTPATH_SPEC.loader.exec_module(_HOTPATH_MODULE)


def test_compare_benchmarks_reports_large_regressions():
    previous = {"rank": {"mean_ms": 10.0, "p95_ms": 20.0}}
    current = {"rank": {"mean_ms": 12.0, "p95_ms": 31.0}}
    warnings = compare(previous, current, warn_percent=30.0)
    assert warnings == ["rank.p95_ms: 20.0000 -> 31.0000 ms (+55.0%)"]


def test_compare_benchmarks_ignores_small_changes():
    previous = {"rank": {"mean_ms": 10.0, "p95_ms": 20.0}}
    current = {"rank": {"mean_ms": 12.0, "p95_ms": 25.0}}
    assert compare(previous, current, warn_percent=30.0) == []


def test_hotpath_p95_uses_nearest_rank_for_small_samples():
    samples = [100.0, 1.0, *([1.0] * 22), 100.0]

    assert _HOTPATH_MODULE._nearest_rank(samples, 0.95) == 100.0
    assert _HOTPATH_MODULE._nearest_rank([7.0], 0.95) == 7.0


def test_persistence_write_contract_matches_committed_evidence(tmp_path):
    expected = json.loads(
        (Path(__file__).resolve().parents[1] / "benchmarks" / "persistence-batching-v1.json")
        .read_text(encoding="utf-8")
    )

    actual = _HOTPATH_MODULE._persistence_write_contract(
        tmp_path,
        operations=320,
        batch_size=32,
    )

    assert actual == expected
