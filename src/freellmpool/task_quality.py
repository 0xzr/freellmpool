"""Local task classification and validated task-specific model evidence.

Task fit is orthogonal to generic benchmark capability. The first deliberately
narrow task is grounded document reading: requests that ask a model to extract
or summarize supplied Markdown without inventing details.

Evidence is provider-neutral and exact-model only. It must come from repeated
runs of the current sanitized fixture; production prompts are never learned
from or persisted.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

TASK_AUTO = "auto"
TASK_GENERAL = "general"
TASK_GROUNDED_READING = "grounded-reading"
TASK_HINTS = (TASK_AUTO, TASK_GENERAL, TASK_GROUNDED_READING)

GROUNDED_FIXTURE_SHA256 = (
    "68256d842856b9a81c5d3f93efa873e23689b69febd8d245a1c0eead7aedff82"
)
_BUNDLED_EVIDENCE = Path(__file__).with_name("task_evidence.json")
_MAX_CLASSIFIER_CHARS = 65_536
_MIN_TRIALS = 3

_GROUNDED_INTENT_RE = re.compile(
    r"\b(?:read|summari[sz]e|extract|tell me what|what (?:it|this) "
    r"contains|according to|based (?:only )?on)\b",
    re.IGNORECASE,
)
_MARKDOWN_STRUCTURE_RE = re.compile(
    r"(?m)^(?:#{1,6}\s+\S|[-*]\s+\S|\|.+\||```)"
)


@dataclass(frozen=True, slots=True)
class TaskResolution:
    task: str
    source: str


def _routing_text(messages: object) -> str:
    """Bounded user/tool text only; system and prior assistant prose cannot steer it."""
    parts: list[str] = []
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"user", "tool"}:
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(
                part["text"]
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
    text = "\n".join(parts)
    if len(text) <= _MAX_CLASSIFIER_CHARS:
        return text
    half = _MAX_CLASSIFIER_CHARS // 2
    return f"{text[:half]}\n{text[-half:]}"


def classify_task(messages: object) -> str:
    """Return a high-confidence local task class without making a model call."""
    text = _routing_text(messages)
    if not _GROUNDED_INTENT_RE.search(text):
        return TASK_GENERAL
    structures = _MARKDOWN_STRUCTURE_RE.findall(text)
    if len(structures) >= 2:
        return TASK_GROUNDED_READING
    return TASK_GENERAL


def task_resolution(messages: object, task: str | None = None) -> TaskResolution:
    """Resolve explicit intent before automatic classification."""
    validate_task(task)
    if task is None or task == TASK_AUTO:
        return TaskResolution(classify_task(messages), "auto")
    if task in {TASK_GENERAL, TASK_GROUNDED_READING}:
        return TaskResolution(task, "explicit")
    raise ValueError(f"unknown task {task!r}; expected one of: {', '.join(TASK_HINTS)}")


def validate_task(task: str | None) -> None:
    if task is not None and task not in TASK_HINTS:
        raise ValueError(
            f"unknown task {task!r}; expected one of: {', '.join(TASK_HINTS)}"
        )


def resolve_task(messages: object, task: str | None = None) -> str:
    return task_resolution(messages, task).task


def user_task_evidence_path() -> Path:
    override = os.environ.get("FREELLMPOOL_TASK_EVIDENCE_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "freellmpool" / "task_evidence.json"


def _fixture_for(task: str) -> str | None:
    if task == TASK_GROUNDED_READING:
        return GROUNDED_FIXTURE_SHA256
    return None


def _read_evidence(path: Path, task: str) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("version") != 1:
        return {}
    scores = data.get("scores")
    raw = scores.get(task, {}) if isinstance(scores, dict) else {}
    fixture = _fixture_for(task)
    if not isinstance(raw, dict) or fixture is None:
        return {}
    out: dict[str, float] = {}
    for model, entry in raw.items():
        if not isinstance(model, str) or not model or not isinstance(entry, dict):
            continue
        source = entry.get("source")
        trials = entry.get("trials")
        passed = entry.get("passed")
        if (
            not isinstance(source, str)
            or not source.strip()
            or entry.get("fixture_sha256") != fixture
            or not isinstance(trials, int)
            or isinstance(trials, bool)
            or trials < _MIN_TRIALS
            or not isinstance(passed, int)
            or isinstance(passed, bool)
            or not 0 <= passed <= trials
        ):
            continue
        raw_score = entry.get("score")
        if not isinstance(raw_score, (int, float, str)) or isinstance(
            raw_score, bool
        ):
            continue
        try:
            score = float(raw_score)
        except ValueError:
            continue
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            continue
        if not math.isclose(score, passed / trials, abs_tol=1e-4):
            continue
        out[model] = score
    return out


@lru_cache(maxsize=24)
def _evidence_cached(
    user_str: str, _user_mtime: int, task: str
) -> Mapping[str, float]:
    table = _read_evidence(_BUNDLED_EVIDENCE, task)
    table.update(_read_evidence(Path(user_str), task))
    return MappingProxyType(table)


def task_evidence_table(task: str) -> Mapping[str, float]:
    """Return exact model identities with current, repeated task evidence."""
    if task == TASK_GENERAL:
        return MappingProxyType({})
    if task not in TASK_HINTS or task == TASK_AUTO:
        raise ValueError(f"unknown resolved task {task!r}")
    user = user_task_evidence_path()
    try:
        mtime = user.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return _evidence_cached(str(user), mtime, task)


def model_task_score(
    model: str, table: Mapping[str, float]
) -> float | None:
    """Exact identity lookup: no family, provider, or semantic alias borrowing."""
    return table.get(model)


def grounded_answer_passes(answer: str, case: Mapping[str, object]) -> bool:
    """Deterministic fixture rubric used to aggregate bounded benchmark trials."""
    lowered = answer.casefold()
    must_include = case.get("must_include")
    must_not_invent = case.get("must_not_invent")
    if not isinstance(must_include, list) or not isinstance(must_not_invent, list):
        raise ValueError("grounded fixture needs must_include and must_not_invent lists")
    return all(
        isinstance(fact, str) and fact.casefold() in lowered for fact in must_include
    ) and all(
        isinstance(claim, str) and claim.casefold() not in lowered
        for claim in must_not_invent
    )
