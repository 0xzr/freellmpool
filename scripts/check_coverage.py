#!/usr/bin/env python3
"""Enforce package line and branch coverage floors independently."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_THRESHOLDS = ROOT / ".coverage-thresholds.json"
METRICS = {"lines", "branches"}


class CoverageInputError(ValueError):
    """Raised when coverage or threshold input cannot be trusted."""


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CoverageInputError(f"Could not read {label} file {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CoverageInputError(f"Invalid JSON in {label} file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CoverageInputError(f"{label.capitalize()} file must contain a JSON object")
    return data


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CoverageInputError(f"{label} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise CoverageInputError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise CoverageInputError(f"{label} must be a finite number")
    return number


def _thresholds(config: dict[str, Any]) -> dict[str, float]:
    raw = config.get("thresholds")
    if not isinstance(raw, dict) or set(raw) != METRICS:
        raise CoverageInputError("thresholds must contain exactly 'lines' and 'branches'")
    thresholds = {name: _finite_number(raw[name], f"thresholds.{name}") for name in METRICS}
    for name, threshold in thresholds.items():
        if not 0 <= threshold <= 100:
            raise CoverageInputError(f"thresholds.{name} must be between 0 and 100")
    return thresholds


def _percentage(totals: dict[str, Any], covered_key: str, total_key: str) -> float:
    missing = [key for key in (covered_key, total_key) if key not in totals]
    if missing:
        raise CoverageInputError(f"Missing coverage totals field(s): {', '.join(missing)}")
    covered = _finite_number(totals[covered_key], f"coverage totals.{covered_key}")
    total = _finite_number(totals[total_key], f"coverage totals.{total_key}")
    if total <= 0:
        raise CoverageInputError(f"coverage totals.{total_key} must be greater than zero")
    if covered < 0 or covered > total:
        raise CoverageInputError(
            f"coverage totals.{covered_key} must be between zero and {total_key}"
        )
    return covered / total * 100


def _coverage_percentages(report: dict[str, Any]) -> dict[str, float]:
    meta = report.get("meta")
    if not isinstance(meta, dict) or meta.get("branch_coverage") is not True:
        raise CoverageInputError("branch coverage is not enabled in the coverage report")
    totals = report.get("totals")
    if not isinstance(totals, dict):
        raise CoverageInputError("coverage report is missing coverage totals")
    return {
        "lines": _percentage(totals, "covered_lines", "num_statements"),
        "branches": _percentage(totals, "covered_branches", "num_branches"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage", type=Path, help="coverage.py JSON report")
    parser.add_argument("--thresholds", type=Path, default=DEFAULT_THRESHOLDS)
    args = parser.parse_args(argv)

    try:
        percentages = _coverage_percentages(_load_object(args.coverage, "coverage"))
        thresholds = _thresholds(_load_object(args.thresholds, "threshold"))
    except CoverageInputError as exc:
        print(f"Coverage gate input error: {exc}", file=sys.stderr)
        return 2

    failures = [
        f"{name} {percentages[name]:.2f}% < {thresholds[name]:.2f}%"
        for name in ("lines", "branches")
        if percentages[name] < thresholds[name]
    ]
    if failures:
        print(f"Coverage gate failed: {'; '.join(failures)}", file=sys.stderr)
        return 1

    results = "; ".join(
        f"{name} {percentages[name]:.2f}% >= {thresholds[name]:.2f}%"
        for name in ("lines", "branches")
    )
    print(f"Coverage gate passed: {results}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
