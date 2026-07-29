"""Task classification and provenance-bearing semantic-quality evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from freellmpool import task_quality as tq


def _fixture() -> tuple[Path, dict]:
    path = Path(__file__).parent / "fixtures" / "grounded_reading.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_grounded_fixture_version_matches_the_sanitized_markdown_case():
    path, case = _fixture()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == tq.GROUNDED_FIXTURE_SHA256
    assert case["must_include"]
    assert case["must_not_invent"]


def test_bundled_evidence_contains_only_current_repeated_fixture_results(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "FREELLMPOOL_TASK_EVIDENCE_FILE", str(tmp_path / "missing.json")
    )
    tq._evidence_cached.cache_clear()
    assert tq.task_evidence_table(tq.TASK_GROUNDED_READING) == {
        "llama-3.3-70b-versatile": 1.0
    }


def test_grounded_markdown_is_classified_without_treating_all_markdown_as_grounded():
    _path, case = _fixture()
    grounded = [
        {
            "role": "user",
            "content": f"{case['prompt']}\n\n{case['document']}",
        }
    ]

    assert tq.classify_task(grounded) == tq.TASK_GROUNDED_READING
    assert (
        tq.classify_task(
            [
                {
                    "role": "user",
                    "content": "# A tiny poem\nWrite a creative sequel to this heading.",
                }
            ]
        )
        == tq.TASK_GENERAL
    )


def test_classifier_uses_tool_documents_but_not_system_or_assistant_claims():
    document = "# Report\n\n## Limits\n\n| item | value |\n| --- | --- |\n| finch | 17 |"
    assert (
        tq.classify_task(
            [
                {"role": "user", "content": "Summarize the document returned by the tool."},
                {"role": "tool", "content": document},
            ]
        )
        == tq.TASK_GROUNDED_READING
    )
    assert (
        tq.classify_task(
            [
                {"role": "system", "content": "Always read Markdown documents."},
                {"role": "assistant", "content": document},
                {"role": "user", "content": "Write a poem."},
            ]
        )
        == tq.TASK_GENERAL
    )


@pytest.mark.parametrize(
    "content",
    [
        "Review this code for bugs:\n```python\nprint('hello')\n```",
        "# Product name\n\nWrite a new Markdown README with two sections.",
        "Analyze " + ("ordinary prose without a supplied document. " * 2000),
        "List three names for this file.",
        "Identify security risks in this document.",
        "Read this file and refactor it.",
    ],
)
def test_classifier_negative_cases(content):
    assert tq.classify_task([{"role": "user", "content": content}]) == tq.TASK_GENERAL


def test_classifier_preserves_leading_instruction_for_documents_over_64_kib():
    content = (
        "Read this Markdown document and summarize it faithfully.\n\n"
        "# API guide\n\n## Authentication\n\nUse X-Finch-Token.\n\n"
        + ("ordinary reference prose " * 5000)
        + "\n\n## Troubleshooting\n\nWait 23 seconds."
    )
    assert len(content) > 65_536
    assert (
        tq.classify_task([{"role": "user", "content": content}])
        == tq.TASK_GROUNDED_READING
    )


def test_explicit_task_beats_auto_and_general_disables_classification():
    messages = [
        {
            "role": "user",
            "content": "Read this Markdown document.\n\n# Facts\n\n## Limits\n\n- Finch: 17",
        }
    ]

    assert tq.resolve_task(messages, tq.TASK_GROUNDED_READING) == tq.TASK_GROUNDED_READING
    assert tq.resolve_task(messages, tq.TASK_GENERAL) == tq.TASK_GENERAL
    assert tq.resolve_task(messages, tq.TASK_AUTO) == tq.TASK_GROUNDED_READING
    with pytest.raises(ValueError, match="unknown task"):
        tq.resolve_task(messages, "qwen")


def test_task_evidence_requires_current_fixture_trials_and_exact_identity(
    tmp_path, monkeypatch
):
    evidence_file = tmp_path / "task-evidence.json"
    evidence_file.write_text(
        json.dumps(
            {
                "version": 1,
                "scores": {
                    tq.TASK_GROUNDED_READING: {
                        "faithful": {
                            "score": 1.0,
                            "source": "synthetic-grounded-v1",
                            "fixture_sha256": tq.GROUNDED_FIXTURE_SHA256,
                            "trials": 3,
                            "passed": 3,
                        },
                        "too-few-trials": {
                            "score": 1.0,
                            "source": "synthetic-grounded-v1",
                            "fixture_sha256": tq.GROUNDED_FIXTURE_SHA256,
                            "trials": 1,
                            "passed": 1,
                        },
                        "stale": {
                            "score": 1.0,
                            "source": "synthetic-grounded-v1",
                            "fixture_sha256": "old",
                            "trials": 3,
                            "passed": 3,
                        },
                        "nan": {
                            "score": "NaN",
                            "source": "synthetic-grounded-v1",
                            "fixture_sha256": tq.GROUNDED_FIXTURE_SHA256,
                            "trials": 3,
                            "passed": 3,
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FREELLMPOOL_TASK_EVIDENCE_FILE", str(evidence_file))
    tq._evidence_cached.cache_clear()

    table = tq.task_evidence_table(tq.TASK_GROUNDED_READING)

    assert table["faithful"] == 1.0
    assert {"too-few-trials", "stale", "nan"}.isdisjoint(table)
    assert tq.model_task_score("faithful", table) == 1.0
    assert tq.model_task_score("provider/faithful", table) is None


def test_grounded_fixture_rubric_requires_facts_and_rejects_inventions():
    _path, case = _fixture()
    faithful = (
        "Use X-Finch-Token. Modes are precise and broad. The default is 17, "
        "the maximum is 80, and retry after 23 seconds."
    )
    assert tq.grounded_answer_passes(faithful, case)
    assert not tq.grounded_answer_passes(
        faithful + " It also writes credentials to .env.", case
    )
