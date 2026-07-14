from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ci_enforces_repository_coverage_floor() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    match = re.search(r"--cov-fail-under=(\d+)", workflow)

    assert match is not None, "CI must enforce an explicit coverage floor"
    assert int(match.group(1)) >= 80
