from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_coverage.py"


def _thresholds() -> dict[str, object]:
    return {"thresholds": {"lines": 80, "branches": 70}}


def _coverage(
    *,
    covered_lines: object = 80,
    num_statements: object = 100,
    covered_branches: object = 7,
    num_branches: object = 10,
    branch_coverage: object = True,
) -> dict[str, object]:
    return {
        "meta": {"branch_coverage": branch_coverage},
        "totals": {
            "covered_lines": covered_lines,
            "num_statements": num_statements,
            "covered_branches": covered_branches,
            "num_branches": num_branches,
        },
    }


def _run_gate(
    tmp_path: Path,
    coverage: object,
    thresholds: object | None = None,
) -> subprocess.CompletedProcess[str]:
    coverage_path = tmp_path / "coverage.json"
    thresholds_path = tmp_path / "thresholds.json"
    coverage_path.write_text(json.dumps(coverage), encoding="utf-8")
    thresholds_path.write_text(json.dumps(thresholds or _thresholds()), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(CHECKER), str(coverage_path), "--thresholds", str(thresholds_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_coverage_gate_accepts_exact_thresholds(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, _coverage())

    assert result.returncode == 0, result.stderr
    assert "lines 80.00% >= 80.00%" in result.stdout
    assert "branches 70.00% >= 70.00%" in result.stdout


@pytest.mark.parametrize(
    ("coverage", "failure"),
    [
        (_coverage(covered_lines=79), "lines 79.00% < 80.00%"),
        (_coverage(covered_branches=6), "branches 60.00% < 70.00%"),
    ],
)
def test_coverage_gate_rejects_each_metric_independently(
    tmp_path: Path,
    coverage: object,
    failure: str,
) -> None:
    result = _run_gate(tmp_path, coverage)

    assert result.returncode == 1
    assert failure in result.stderr


@pytest.mark.parametrize(
    ("coverage", "thresholds", "error"),
    [
        ({"meta": {"branch_coverage": True}, "totals": {}}, None, "missing coverage"),
        (_coverage(branch_coverage=False), None, "branch coverage is not enabled"),
        (_coverage(covered_lines=float("nan")), None, "finite number"),
        (_coverage(covered_lines=10**400), None, "finite number"),
        (_coverage(num_statements=0), None, "greater than zero"),
        (_coverage(), {"thresholds": {"lines": 80, "branches": 70, "functions": 80}}, "exactly"),
        (_coverage(), {"thresholds": {"lines": 80}}, "exactly"),
    ],
)
def test_coverage_gate_rejects_invalid_inputs(
    tmp_path: Path,
    coverage: object,
    thresholds: object | None,
    error: str,
) -> None:
    result = _run_gate(tmp_path, coverage, thresholds)

    assert result.returncode == 2
    assert error in result.stderr.lower()


def test_coverage_gate_rejects_missing_and_malformed_files(tmp_path: Path) -> None:
    missing = subprocess.run(
        [sys.executable, str(CHECKER), str(tmp_path / "missing.json")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing.returncode == 2
    assert "could not read" in missing.stderr.lower()

    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text("{broken", encoding="utf-8")
    malformed = subprocess.run(
        [sys.executable, str(CHECKER), str(coverage_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert malformed.returncode == 2
    assert "invalid json" in malformed.stderr.lower()
