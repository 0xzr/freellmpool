"""Tests for the local foreground job queue (WU-009)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from freellmpool import jobs
from freellmpool.artifacts import RunRecordStore
from freellmpool.jobs import (
    JOB_EVENT_CANCELLED,
    JOB_EVENT_COMPLETED,
    JOB_EVENT_FAILED,
    JOB_EVENT_QUEUED,
    JOB_EVENT_STARTED,
    JOB_KIND_ASK,
    JOB_KIND_RECIPE,
    JOB_KNOWN_EVENT_TYPES,
    JOB_SCHEMA_VERSION,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    DuplicateJobError,
    Job,
    JobError,
    JobEvent,
    JobSpec,
    JobStore,
    UnknownJobError,
    default_jobs_path,
    render_jobs,
    render_run_plan,
    render_run_summary,
    run_pending_jobs,
)
from freellmpool.models import Reply

# ---- helpers --------------------------------------------------------


def _fixed_clock() -> Callable[[], datetime]:
    base = datetime(2026, 6, 19, 12, 0, 0, tzinfo=UTC)
    ticks = {"n": 0}

    def _clock() -> datetime:
        ticks["n"] += 1
        return base.fromtimestamp(base.timestamp() + ticks["n"], tz=UTC)

    return _clock


def _store(tmp_path: Path) -> JobStore:
    counter = {"n": 0}

    def _id() -> str:
        counter["n"] += 1
        return f"job-{counter['n']:04d}"

    return JobStore(tmp_path / "jobs.jsonl", clock=_fixed_clock(), id_factory=_id)


def _seq_ids(n: int) -> Callable[[], str]:
    counter = {"n": 0}

    def _id() -> str:
        counter["n"] += 1
        return f"job-{counter['n']:04d}"

    # pre-increment so the first id is job-0001; verify by calling once
    for _ in range(n):
        counter["n"] += 1
    counter["n"] = 0
    return _id


@dataclass
class _FakeRecipeRun:
    output: str = "ok"
    provider_id: str | None = "fake"
    model: str | None = "critic"
    prompt: str = "rendered-prompt"

    @property
    def recipe(self):
        return None


def _fake_recipes_module(behaviour: Callable[[Job], _FakeRecipeRun] | None = None) -> Any:
    """Build a stand-in for freellmpool.recipes with the symbols the runner uses."""

    class _StubRecipeModule:
        def get_recipe(self, name: str):
            return _StubRecipe(name)

        def collect_recipe_input(self, recipe, *, prompt, stdin, input_file, path):
            return prompt or "stub-input", path

        def run_recipe(self, pool, recipe, *, input_text, path, **kwargs):
            if behaviour is not None:
                return behaviour(recipe)
            return _FakeRecipeRun(output=f"ran:{recipe.name}:{input_text}")

        def write_recipe_record(self, run, *, store=None):
            s = store or RunRecordStore()
            return s.append_new(
                kind="recipe",
                title=f"recipe {run.output}",
                prompt=run.prompt,
                output=run.output,
                provider_id=run.provider_id,
                model=run.model,
                recipe="pr-review",
            )

    class _StubRecipe:
        def __init__(self, name: str):
            self.name = name
            self.version = "1.0.0"
            self.role = "critic"

    return _StubRecipeModule()


class _FakePool:
    def __init__(self, replies: list[str] | None = None):
        self.replies = replies or ["ok"]
        self.calls: list[dict[str, Any]] = []

    def ask(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": dict(kwargs)})
        text = self.replies.pop(0) if self.replies else "ok"
        return Reply(text=text, provider_id="fake", model="critic", raw={})


def _pool_factory(pool: _FakePool | None = None) -> Callable[[], Any]:
    pool = pool or _FakePool()
    return lambda: pool


# ---- 1. Add / list / run / watch / cancel commands work -------------


def test_cli_jobs_add_list_watch_cancel_run_round_trip(monkeypatch, tmp_path, capsys):
    """`freellmpool jobs add/list/run/watch/cancel` all dispatch via the CLI."""
    monkeypatch.setenv("FREELLMPOOL_JOBS_PATH", str(tmp_path / "jobs.jsonl"))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")

    from freellmpool.cli import main

    rc = main(["jobs", "add", "--recipe", "pr-review", "patch text"])
    assert rc == 0
    job_id = capsys.readouterr().out.strip()
    assert job_id

    rc = main(["jobs", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert job_id in out
    assert "pending" in out

    rc = main(["jobs", "watch"])
    out = capsys.readouterr().out
    assert rc == 0
    assert job_id in out

    # Cancel a fresh job and verify the tombstone appears
    rc = main(["jobs", "add", "--recipe", "pr-review", "another"])
    other_id = capsys.readouterr().out.strip()
    rc = main(["jobs", "cancel", other_id])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cancelled" in out

    # Run the first one (which is still pending); needs a fake pool/recipes
    pool = _FakePool()
    import freellmpool.jobs as jobs_mod
    import freellmpool.recipes as recipes_mod

    monkeypatch.setattr(jobs_mod, "_default_pool_factory", lambda _m: (lambda: pool))
    monkeypatch.setattr(jobs_mod, "_import_recipes", lambda: recipes_mod)
    monkeypatch.setattr(jobs_mod, "write_report", lambda *a, **k: Path("/dev/null"))

    rc = main(["jobs", "run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "completed: 1" in out


# ---- 2. Append-only JSONL events with schema 1.0.0 -----------------


def test_jobs_jsonl_events_have_required_fields(tmp_path):
    store = _store(tmp_path)
    job = store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        )
    )

    lines = store.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["schema_version"] == JOB_SCHEMA_VERSION == "1.0.0"
    assert payload["event"] == JOB_EVENT_QUEUED
    assert payload["status"] == JOB_STATUS_PENDING
    assert payload["job_id"] == job.job_id
    assert payload["created_at"].endswith("Z")
    assert payload["spec"]["recipe"] == "pr-review"
    assert payload["spec"]["prompt"] == "x"
    assert payload["attempt"] == 0
    assert payload["seq"] == 1


def test_jobs_event_rejects_wrong_schema(tmp_path):
    path = tmp_path / "jobs.jsonl"
    path.write_text(
        json.dumps({"schema_version": "0.0.9", "event": "queued", "job_id": "x"}) + "\n",
        encoding="utf-8",
    )
    store = JobStore(path)
    assert store.events() == []
    assert store.jobs() == []


# ---- 3. Cancellation is a new event, not a mutation -----------------


def test_cancel_appends_new_tombstone_and_preserves_earlier_events(tmp_path):
    store = _store(tmp_path)
    job = store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        )
    )

    store.cancel(job.job_id)

    raw_lines = store.path.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 2
    queued = json.loads(raw_lines[0])
    cancelled = json.loads(raw_lines[1])
    assert queued["status"] == JOB_STATUS_PENDING
    assert cancelled["event"] == JOB_EVENT_CANCELLED
    assert cancelled["status"] == JOB_STATUS_CANCELLED
    # The original queued record was not mutated.
    assert queued["status"] == JOB_STATUS_PENDING
    assert queued["seq"] < cancelled["seq"]


def test_cancel_of_unknown_job_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(UnknownJobError):
        store.cancel("job-missing")


def test_cancel_is_idempotent(tmp_path):
    store = _store(tmp_path)
    job = store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        )
    )
    store.cancel(job.job_id)
    again = store.cancel(job.job_id)
    assert again.status == JOB_STATUS_CANCELLED
    # Only the first cancel appended a tombstone; the second was a no-op.
    raw_lines = store.path.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 2


# ---- 4. Watch renders state without a daemon ------------------------


def test_watch_uses_replayed_state_only(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FREELLMPOOL_JOBS_PATH", str(tmp_path / "jobs.jsonl"))
    # Stale JSONL state from a previous process must show up without any
    # daemon, socket, or external process holding the lock.
    other_store = _store(tmp_path)
    other_store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        )
    )
    # open a new store fresh, no daemon running
    from freellmpool.cli import main

    assert main(["jobs", "watch"]) == 0
    out = capsys.readouterr().out
    assert "pr-review" in out
    assert "pending" in out


# ---- 5. Queue survives process restart by replaying JSONL ----------


def test_queue_state_survives_process_restart(tmp_path):
    first = _store(tmp_path)
    job_a = first.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "a"},
        )
    )
    job_b = first.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "b"},
        )
    )

    # New process: fresh store instance pointing at the same file.
    second = JobStore(tmp_path / "jobs.jsonl")
    replayed = {job.job_id: job for job in second.jobs()}

    assert set(replayed) == {job_a.job_id, job_b.job_id}
    assert replayed[job_a.job_id].status == JOB_STATUS_PENDING
    assert replayed[job_b.job_id].status == JOB_STATUS_PENDING


# ---- 6. Cancelled jobs stay cancelled after replay ------------------


def test_cancelled_jobs_remain_cancelled_after_replay(tmp_path):
    store = _store(tmp_path)
    job = store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        )
    )
    store.cancel(job.job_id)

    reopened = JobStore(tmp_path / "jobs.jsonl")
    replayed = reopened.get(job.job_id)
    assert replayed is not None
    assert replayed.status == JOB_STATUS_CANCELLED
    assert replayed.is_terminal is True
    assert any(event.event == JOB_EVENT_CANCELLED for event in replayed.events)


# ---- 7. Duplicates create distinct jobs by default ------------------


def test_duplicate_submissions_are_distinct_without_dedupe(tmp_path):
    store = _store(tmp_path)
    first = store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "patch"},
        )
    )
    second = store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "patch"},
        )
    )
    third = store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "patch"},
        )
    )

    ids = {first.job_id, second.job_id, third.job_id}
    assert len(ids) == 3
    assert store.pending().__len__() == 3


def test_optional_dedupe_key_rejects_repeat(tmp_path):
    store = _store(tmp_path)
    store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review"},
            dedupe_key="pr-review",
        )
    )
    with pytest.raises(DuplicateJobError):
        store.add(
            JobSpec(
                kind=JOB_KIND_RECIPE,
                payload={"kind": "recipe", "recipe": "pr-review"},
                dedupe_key="pr-review",
            )
        )


# ---- 8. Failed jobs preserve errors and don't block others ---------


def test_failed_jobs_preserve_error_and_do_not_block_others(tmp_path):
    pool = _FakePool(["ok"])  # always returns ok when called
    recipes = _fake_recipes_module(
        behaviour=lambda recipe: _FakeRecipeRun(output=f"ran:{recipe.name}")
    )

    def boom_recipe(_pool, recipe, *, input_text, path, **kwargs):
        if recipe.name == "bad":
            raise RuntimeError("provider exploded")
        return _FakeRecipeRun(output=f"ok:{recipe.name}:{input_text}")

    recipes.run_recipe = boom_recipe  # type: ignore[assignment]
    recipes.write_recipe_record = lambda run, *, store=None: store.append_new(  # type: ignore[assignment]
        kind="recipe", title="t", prompt=run.prompt, output=run.output, recipe="pr-review"
    )

    # No recipes module report path actually executes here; bypass it.
    import freellmpool.jobs as jobs_mod

    write_report_calls: list[str] = []

    def _fake_write_report(record, fmt, *, store):
        write_report_calls.append(record.run_id)
        return Path("/dev/null")

    jobs_mod.write_report = _fake_write_report

    store = _store(tmp_path)
    job_a = store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "good", "prompt": "a"},
        )
    )
    job_b = store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "bad", "prompt": "b"},
        )
    )
    job_c = store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "good", "prompt": "c"},
        )
    )

    outcome = run_pending_jobs(
        store,
        pool_factory=_pool_factory(pool),
        recipes_module=recipes,
    )

    a_after = store.get(job_a.job_id)
    b_after = store.get(job_b.job_id)
    c_after = store.get(job_c.job_id)
    assert a_after is not None and a_after.status == JOB_STATUS_COMPLETED
    assert b_after is not None and b_after.status == JOB_STATUS_FAILED
    assert c_after is not None and c_after.status == JOB_STATUS_COMPLETED
    assert b_after.last_error and "provider exploded" in b_after.last_error

    assert {job.job_id for job in outcome.completed} == {job_a.job_id, job_c.job_id}
    assert {job.job_id for job in outcome.failed} == {job_b.job_id}

    # JSONL preserves the original queued events intact and appends new ones.
    raw = store.path.read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line) for line in raw]
    assert [row["event"] for row in parsed] == [
        JOB_EVENT_QUEUED,
        JOB_EVENT_QUEUED,
        JOB_EVENT_QUEUED,
        JOB_EVENT_STARTED,
        JOB_EVENT_COMPLETED,  # job_a
        JOB_EVENT_STARTED,
        JOB_EVENT_FAILED,     # job_b (error preserved)
        JOB_EVENT_STARTED,
        JOB_EVENT_COMPLETED,  # job_c
    ]
    failed_event = next(row for row in parsed if row["event"] == JOB_EVENT_FAILED)
    assert "provider exploded" in failed_event["error"]


# ---- 9. --dry-run prints order without mutating ---------------------


def test_dry_run_prints_plan_and_mutates_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FREELLMPOOL_JOBS_PATH", str(tmp_path / "jobs.jsonl"))
    store = _store(tmp_path)
    store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "a"},
        )
    )
    store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "b"},
        )
    )

    from freellmpool.cli import main

    assert main(["jobs", "run", "--dry-run"]) == 0
    plan = capsys.readouterr().out
    assert "Execution plan" in plan
    assert "pr-review" in plan

    after = store.path.read_text(encoding="utf-8").splitlines()
    assert len(after) == 2, f"dry-run must not append events; got {after!r}"


def test_run_plan_renderer_includes_pending_jobs(tmp_path):
    store = _store(tmp_path)
    store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        )
    )
    output = render_run_plan(store.pending())
    assert "job-" in output
    assert "recipe:pr-review" in output


# ---- 10. --max-failures halts after N consecutive failures ---------


def test_max_failures_halts_after_n_consecutive(tmp_path):
    pool = _FakePool(["ok"])

    def always_fail(_pool, recipe, *, input_text, path, **kwargs):
        raise RuntimeError("nope")

    recipes = _fake_recipes_module()
    recipes.run_recipe = always_fail  # type: ignore[assignment]
    recipes.write_recipe_record = lambda run, *, store=None: store.append_new(  # type: ignore[assignment]
        kind="recipe", title="t", prompt=run.prompt, output="unused", recipe="pr-review"
    )

    import freellmpool.jobs as jobs_mod

    jobs_mod.write_report = lambda *a, **k: Path("/dev/null")

    store = _store(tmp_path)
    pending_ids = [
        store.add(
            JobSpec(
                kind=JOB_KIND_RECIPE,
                payload={"kind": "recipe", "recipe": "pr-review", "prompt": str(i)},
            )
        ).job_id
        for i in range(5)
    ]

    outcome = run_pending_jobs(
        store,
        pool_factory=_pool_factory(pool),
        recipes_module=recipes,
        max_failures=2,
    )

    assert outcome.halted_by_max_failures is True
    assert outcome.consecutive_failures == 2
    assert len(outcome.failed) == 2
    # The remaining pending jobs are left intact and unrelated to the halt.
    remaining_pending = {job.job_id for job in store.pending()}
    assert remaining_pending == set(pending_ids[2:])

    # The runner did not corrupt unrelated queued records: their queued
    # events still appear at the top of the JSONL untouched.
    parsed = [json.loads(line) for line in store.path.read_text(encoding="utf-8").splitlines()]
    queued_records = [row for row in parsed if row["event"] == JOB_EVENT_QUEUED]
    assert len(queued_records) == 5
    for record in queued_records:
        assert record["status"] == JOB_STATUS_PENDING


def test_max_failures_one_aborts_immediately(tmp_path):
    pool = _FakePool(["ok"])

    def always_fail(_pool, recipe, *, input_text, path, **kwargs):
        raise RuntimeError("nope")

    recipes = _fake_recipes_module()
    recipes.run_recipe = always_fail  # type: ignore[assignment]
    recipes.write_recipe_record = lambda run, *, store=None: store.append_new(  # type: ignore[assignment]
        kind="recipe", title="t", prompt=run.prompt, output="unused", recipe="pr-review"
    )

    import freellmpool.jobs as jobs_mod

    jobs_mod.write_report = lambda *a, **k: Path("/dev/null")

    store = _store(tmp_path)
    for i in range(3):
        store.add(
            JobSpec(
                kind=JOB_KIND_RECIPE,
                payload={"kind": "recipe", "recipe": "pr-review", "prompt": str(i)},
            )
        )

    outcome = run_pending_jobs(
        store,
        pool_factory=_pool_factory(pool),
        recipes_module=recipes,
        max_failures=1,
    )
    assert outcome.halted_by_max_failures is True
    assert outcome.consecutive_failures == 1
    assert len(outcome.failed) == 1
    assert store.pending().__len__() == 2


def test_max_failures_resets_after_success(tmp_path):
    pool = _FakePool(["ok"])

    fail_count = {"n": 0}

    def maybe_fail(_pool, recipe, *, input_text, path, **kwargs):
        fail_count["n"] += 1
        if fail_count["n"] == 1:
            raise RuntimeError("first")
        return _FakeRecipeRun(output="ok")

    recipes = _fake_recipes_module()
    recipes.run_recipe = maybe_fail  # type: ignore[assignment]
    recipes.write_recipe_record = lambda run, *, store=None: store.append_new(  # type: ignore[assignment]
        kind="recipe", title="t", prompt=run.prompt, output=run.output, recipe="pr-review"
    )

    import freellmpool.jobs as jobs_mod

    jobs_mod.write_report = lambda *a, **k: Path("/dev/null")

    store = _store(tmp_path)
    for i in range(3):
        store.add(
            JobSpec(
                kind=JOB_KIND_RECIPE,
                payload={"kind": "recipe", "recipe": "pr-review", "prompt": str(i)},
            )
        )

    outcome = run_pending_jobs(
        store,
        pool_factory=_pool_factory(pool),
        recipes_module=recipes,
        max_failures=2,
    )
    # 1 failure, 1 success (resets), 1 failure, 1 success -> not halted.
    assert outcome.halted_by_max_failures is False
    assert outcome.consecutive_failures == 0
    assert len(outcome.completed) == 2
    assert len(outcome.failed) == 1


# ---- 11. Reports written via WU-008 helpers ------------------------


def test_completed_recipe_jobs_write_reports_via_wu008(tmp_path):
    pool = _FakePool(["ok"])
    recipes = _fake_recipes_module()

    record_store = RunRecordStore(
        tmp_path / "records.jsonl",
        reports_dir=tmp_path / "reports",
        clock=_fixed_clock(),
    )

    captured_reports: list[tuple[str, str]] = []

    def _spy_write_report(record, fmt, *, store):
        captured_reports.append((record.run_id, fmt))
        path = record_store.report_path(record.run_id, fmt)
        # Actually write the report so callers can rely on the WU-008 side effect.
        from freellmpool.reports import render_markdown_report

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown_report(record), encoding="utf-8")
        return path

    import freellmpool.jobs as jobs_mod

    jobs_mod.write_report = _spy_write_report

    store = _store(tmp_path)
    store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        )
    )

    outcome = run_pending_jobs(
        store,
        pool_factory=_pool_factory(pool),
        recipes_module=recipes,
        record_store=record_store,
    )

    assert len(outcome.completed) == 1
    assert len(captured_reports) == 1
    run_id, fmt = captured_reports[0]
    assert fmt == "md"
    # The WU-008 helper wrote the markdown report to the deterministic path.
    md_path = record_store.report_path(run_id, "md")
    assert md_path.exists()
    body = md_path.read_text(encoding="utf-8")
    assert run_id in body
    # The job event records the run_id of the produced report.
    completed_event = next(
        event for event in store.events() if event.event == JOB_EVENT_COMPLETED
    )
    assert completed_event.run_id == run_id


# ---- 12. Default path and env override ------------------------------


def test_default_jobs_path_uses_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("FREELLMPOOL_JOBS_PATH", str(tmp_path / "override.jsonl"))
    assert default_jobs_path() == tmp_path / "override.jsonl"


def test_default_jobs_path_falls_back_to_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("FREELLMPOOL_JOBS_PATH", raising=False)
    monkeypatch.setenv("FREELLMPOOL_DATA_DIR", str(tmp_path / "data"))
    assert default_jobs_path() == tmp_path / "data" / "jobs.jsonl"


def test_store_creates_parent_dir(tmp_path):
    nested = tmp_path / "deep" / "jobs.jsonl"
    store = JobStore(nested)
    store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        )
    )
    assert nested.exists()


# ---- Ask-job path --------------------------------------------------


def test_ask_job_records_completion_and_output(tmp_path):
    pool = _FakePool(["hello world"])
    store = _store(tmp_path)
    job = store.add(
        JobSpec(
            kind=JOB_KIND_ASK,
            payload={
                "kind": "ask",
                "role": "summarizer",
                "prompt": "summarize this",
                "max_tokens": 128,
                "timeout": 5.0,
            },
        )
    )

    outcome = run_pending_jobs(store, pool_factory=_pool_factory(pool))
    assert len(outcome.completed) == 1
    after = store.get(job.job_id)
    assert after is not None and after.status == JOB_STATUS_COMPLETED
    assert after.output == "hello world"
    # The pool received our prompt + role system prefix.
    assert pool.calls and "summarize this" in pool.calls[0]["prompt"]
    assert "summarize" in pool.calls[0]["kwargs"]["system"].lower()


def test_ask_job_failure_path_records_error(tmp_path):
    class BoomPool(_FakePool):
        def ask(self, prompt, **kwargs):
            raise RuntimeError("provider down")

    store = _store(tmp_path)
    job = store.add(
        JobSpec(
            kind=JOB_KIND_ASK,
            payload={"kind": "ask", "role": None, "prompt": "x"},
        )
    )
    outcome = run_pending_jobs(store, pool_factory=_pool_factory(BoomPool()))
    assert len(outcome.failed) == 1
    after = store.get(job.job_id)
    assert after is not None
    assert after.status == JOB_STATUS_FAILED
    assert after.last_error and "provider down" in after.last_error


# ---- Renderers ------------------------------------------------------


def test_render_jobs_includes_status_and_label(tmp_path):
    store = _store(tmp_path)
    store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        )
    )
    text = render_jobs(store.jobs())
    assert "job id" in text
    assert "pending" in text
    assert "recipe:pr-review" in text


def test_render_run_summary_includes_halt_flag():
    outcome = jobs.RunOutcome(
        completed=(),
        failed=(),
        cancelled=(),
        pending=(),
        consecutive_failures=3,
        halted_by_max_failures=True,
    )
    text = render_run_summary(outcome)
    assert "halted" in text


def test_event_serialization_round_trips():
    event = JobEvent(
        seq=7,
        event=JOB_EVENT_QUEUED,
        job_id="job-xyz",
        created_at="2026-06-19T12:00:00Z",
        status=JOB_STATUS_PENDING,
        spec={"kind": "recipe", "recipe": "pr-review"},
        attempt=0,
        attempt_metadata={"number": 0},
    )
    data = event.to_dict()
    assert data["schema_version"] == JOB_SCHEMA_VERSION
    again = JobEvent.from_dict(data)
    assert again == event


def test_run_outcome_total_processed_counts_completed_and_failed():
    outcome = jobs.RunOutcome(
        completed=("a", "b"),
        failed=("c",),
        cancelled=(),
        pending=("d",),
        consecutive_failures=0,
        halted_by_max_failures=False,
    )
    assert outcome.total_processed == 3
    assert outcome.had_failures is True


# ---- CLI behavior edge cases ----------------------------------------


def test_cli_jobs_add_without_recipe_or_role_errors(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FREELLMPOOL_JOBS_PATH", str(tmp_path / "jobs.jsonl"))
    from freellmpool.cli import main

    rc = main(["jobs", "add"])
    assert rc == 2
    assert "recipe" in capsys.readouterr().err or "role" in capsys.readouterr().err


def test_cli_jobs_add_role_requires_prompt(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FREELLMPOOL_JOBS_PATH", str(tmp_path / "jobs.jsonl"))
    from freellmpool.cli import main

    rc = main(["jobs", "add", "--role", "summarizer"])
    assert rc == 2
    assert "prompt" in capsys.readouterr().err


def test_cli_jobs_add_ask_job_uses_pool_ask(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FREELLMPOOL_JOBS_PATH", str(tmp_path / "jobs.jsonl"))
    monkeypatch.setattr("freellmpool.cli._read_stdin", lambda: "")
    pool = _FakePool(["answer"])
    import freellmpool.jobs as jobs_mod

    jobs_mod._default_pool_factory = lambda _m: (lambda: pool)

    from freellmpool.cli import main

    rc = main(["jobs", "add", "--role", "summarizer", "hi"])
    assert rc == 0
    job_id = capsys.readouterr().out.strip()

    rc = main(["jobs", "run"])
    assert rc == 0

    after = JobStore(tmp_path / "jobs.jsonl").get(job_id)
    assert after is not None and after.status == JOB_STATUS_COMPLETED
    assert after.output == "answer"


def test_cli_jobs_run_returns_5_when_halted_by_max_failures(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("FREELLMPOOL_JOBS_PATH", str(tmp_path / "jobs.jsonl"))
    pool = _FakePool(["ok"])

    def always_fail(_pool, recipe, *, input_text, path, **kwargs):
        raise RuntimeError("nope")

    recipes = _fake_recipes_module()
    recipes.run_recipe = always_fail  # type: ignore[assignment]
    recipes.write_recipe_record = lambda run, *, store=None: store.append_new(  # type: ignore[assignment]
        kind="recipe", title="t", prompt=run.prompt, output="unused", recipe="pr-review"
    )

    import freellmpool.jobs as jobs_mod

    jobs_mod._default_pool_factory = lambda _m: (lambda: pool)
    jobs_mod._import_recipes = lambda: recipes
    jobs_mod.write_report = lambda *a, **k: Path("/dev/null")

    store = JobStore(tmp_path / "jobs.jsonl", id_factory=_seq_ids(0))
    counter = {"n": 0}

    def _id() -> str:
        counter["n"] += 1
        return f"job-{counter['n']:04d}"

    store = JobStore(tmp_path / "jobs.jsonl", id_factory=_id)
    for i in range(2):
        store.add(
            JobSpec(
                kind=JOB_KIND_RECIPE,
                payload={"kind": "recipe", "recipe": "pr-review", "prompt": str(i)},
            )
        )

    from freellmpool.cli import main

    rc = main(["jobs", "run", "--max-failures", "1"])
    assert rc == 5
    out = capsys.readouterr().out
    assert "halted" in out


# ---- WU-009 edge cases ---------------------------------------------
#
# The five tests below cover the explicit edge cases the orchestrator
# flagged as missing. Each test asserts the *contract*, not the
# implementation detail, so they remain valid even if the runner is
# refactored as long as the externally observable behaviour holds.


def test_job_event_to_dict_emits_id_and_attempt_metadata():
    """``to_dict`` writes both top-level ``id`` and ``job_id``, plus
    ``attempt_metadata`` carrying the attempt number.

    The WU-009 contract is that future readers should be able to address
    an event via either a stable ``id`` alias or the legacy ``job_id``
    field. ``attempt_metadata`` is the expansion point for richer
    per-attempt data (resumed, previous_event_seq, …) and the integer
    ``attempt`` must always be mirrored under ``attempt_metadata.number``
    so the two never drift on append.
    """
    event = JobEvent(
        seq=3,
        event=JOB_EVENT_STARTED,
        job_id="job-xyz",
        created_at="2026-06-19T12:00:03Z",
        status=JOB_STATUS_RUNNING,
        spec={"kind": "recipe", "recipe": "pr-review"},
        attempt=2,
        attempt_metadata={"number": 2, "resumed": True},
    )

    payload = event.to_dict()

    assert payload["id"] == "job-xyz"
    assert payload["job_id"] == "job-xyz"
    assert payload["id"] == payload["job_id"]
    assert payload["attempt"] == 2
    assert payload["attempt_metadata"]["number"] == 2
    assert payload["attempt_metadata"]["resumed"] is True


def test_job_event_from_dict_accepts_top_level_id_without_job_id():
    """``from_dict`` must accept events that carry only top-level ``id``.

    Older log writers (and external tools that synthesise events by
    hand) may emit only ``id`` and omit the legacy ``job_id`` alias. The
    replay path should treat ``id`` and ``job_id`` as equivalent and
    succeed without raising ``JobError``.
    """
    payload = {
        "schema_version": JOB_SCHEMA_VERSION,
        "seq": 1,
        "event": JOB_EVENT_QUEUED,
        "id": "job-legacy",
        # NOTE: no "job_id" key on purpose
        "created_at": "2026-06-19T12:00:00Z",
        "status": JOB_STATUS_PENDING,
        "spec": {"kind": "recipe", "recipe": "pr-review"},
        "attempt": 0,
        "attempt_metadata": {"number": 0},
    }

    event = JobEvent.from_dict(payload)

    assert event.job_id == "job-legacy"
    assert event.seq == 1
    assert event.event == JOB_EVENT_QUEUED
    assert event.attempt == 0
    assert event.attempt_metadata["number"] == 0


def test_job_store_iteration_is_fifo_not_lexicographic(tmp_path):
    """``jobs()`` and ``pending()`` order by append sequence, not job_id.

    Use an ``id_factory`` that returns deliberately non-sorted ids
    (``job-z``, ``job-a``, ``job-m``). If the ordering fell back to a
    lexicographic sort on ``job_id`` the result would be
    ``job-a, job-m, job-z``; the FIFO contract demands the original
    append order ``job-z, job-a, job-m``.
    """
    sequence = iter(["job-z", "job-a", "job-m"])

    def _id_factory() -> str:
        return next(sequence)

    store = JobStore(tmp_path / "jobs.jsonl", id_factory=_id_factory)

    added_ids: list[str] = []
    for recipe in ("one", "two", "three"):
        added = store.add(
            JobSpec(
                kind=JOB_KIND_RECIPE,
                payload={"kind": "recipe", "recipe": "pr-review", "prompt": recipe},
            )
        )
        added_ids.append(added.job_id)

    assert added_ids == ["job-z", "job-a", "job-m"]

    observed = [job.job_id for job in store.jobs()]
    assert observed == ["job-z", "job-a", "job-m"], (
        "jobs() must iterate in FIFO append order, not by job_id"
    )

    pending_ids = [job.job_id for job in store.pending()]
    assert pending_ids == ["job-z", "job-a", "job-m"], (
        "pending() must iterate in FIFO append order, not by job_id"
    )


def test_cli_jobs_run_dry_run_with_limit_one_prints_only_first_job(monkeypatch, tmp_path):
    """``freellmpool jobs run --dry-run --limit 1`` prints only the first
    job in FIFO order and leaves the JSONL queue file unchanged.

    This pins the dry-run + limit interaction end-to-end through the
    real CLI entry point. The ``--limit 1`` slice must come from the
    same FIFO snapshot ``run_pending_jobs`` would consume, and the
    queue file bytes must be identical before and after the invocation.
    """
    import contextlib
    import io

    monkeypatch.setenv("FREELLMPOOL_JOBS_PATH", str(tmp_path / "jobs.jsonl"))
    # Deliberately non-sorted ids so a lexicographic sort would
    # shuffle the order; FIFO should still produce ``job-z`` first.
    ids = iter(["job-z", "job-a", "job-m"])
    store = JobStore(
        tmp_path / "jobs.jsonl",
        clock=_fixed_clock(),
        id_factory=lambda: next(ids),
    )
    added: list[str] = []
    for prompt in ("alpha", "beta", "gamma"):
        added.append(
            store.add(
                JobSpec(
                    kind=JOB_KIND_RECIPE,
                    payload={
                        "kind": "recipe",
                        "recipe": "pr-review",
                        "prompt": prompt,
                    },
                )
            ).job_id
        )
    assert added == ["job-z", "job-a", "job-m"]

    path = store.path
    before_bytes = path.read_bytes()
    assert len(before_bytes.splitlines()) == 3

    from freellmpool.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["jobs", "run", "--dry-run", "--limit", "1"])
    assert rc == 0
    output = buf.getvalue()

    # The first FIFO job is the only one rendered in the dry-run
    # plan. The remaining two jobs must NOT appear in the output.
    assert added[0] in output
    assert added[1] not in output
    assert added[2] not in output
    assert "Execution plan" in output

    after_bytes = path.read_bytes()
    assert after_bytes == before_bytes, (
        "dry-run must not append events to the queue file"
    )


def test_cli_jobs_run_dry_run_rejects_invalid_max_failures(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FREELLMPOOL_JOBS_PATH", str(tmp_path / "jobs.jsonl"))
    from freellmpool.cli import main

    for bad in ("0", "-1"):
        capsys.readouterr()
        assert main(["jobs", "run", "--dry-run", "--max-failures", bad]) == 3
        assert "--max-failures" in capsys.readouterr().err


def test_stranded_started_job_is_resumed_and_completed(tmp_path):
    """A queue with ``queued`` + ``started`` but no terminal event must be
    retryable after replay.

    Simulate a previous ``freellmpool jobs run`` that crashed after
    appending ``started`` but before appending ``completed``/``failed``.
    The next ``run_pending_jobs`` invocation must:

      1. pick the stranded job up in ``pending()``,
      2. append a *new* ``started`` event with ``attempt + 1`` and a
         ``resumed: True`` marker in ``attempt_metadata``, and
      3. append a terminal ``completed`` (or ``failed``) event so the
         job leaves the non-terminal state.

    The original ``queued`` and ``started`` events must remain in the
    JSONL untouched — replay safety depends on it.
    """
    path = tmp_path / "jobs.jsonl"
    # Pre-seed the JSONL with a queued event and a stranded started
    # event from a crashed previous run. seq 1 = queued (attempt 0),
    # seq 2 = started (attempt 1) without a terminal follow-up.
    queued = {
        "schema_version": JOB_SCHEMA_VERSION,
        "seq": 1,
        "event": JOB_EVENT_QUEUED,
        "id": "job-stranded",
        "job_id": "job-stranded",
        "created_at": "2026-06-19T12:00:00Z",
        "status": JOB_STATUS_PENDING,
        "spec": {"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        "attempt": 0,
        "attempt_metadata": {"number": 0},
    }
    started = {
        "schema_version": JOB_SCHEMA_VERSION,
        "seq": 2,
        "event": JOB_EVENT_STARTED,
        "id": "job-stranded",
        "job_id": "job-stranded",
        "created_at": "2026-06-19T12:00:01Z",
        "status": JOB_STATUS_RUNNING,
        "spec": {"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        "attempt": 1,
        "attempt_metadata": {"number": 1},
    }
    path.write_text(
        json.dumps(queued) + "\n" + json.dumps(started) + "\n", encoding="utf-8"
    )

    # Stand-in pool + recipes module so the runner can complete the job.
    pool = _FakePool(["ok"])
    recipes = _fake_recipes_module()

    import freellmpool.jobs as jobs_mod

    jobs_mod.write_report = lambda *a, **k: Path("/dev/null")

    store = JobStore(path, clock=_fixed_clock())
    # Sanity: the stranded job is non-terminal and therefore retryable.
    assert store.pending()
    assert store.pending()[0].status == "running"

    outcome = run_pending_jobs(
        store,
        pool_factory=_pool_factory(pool),
        recipes_module=recipes,
    )

    assert len(outcome.completed) == 1
    after = store.get("job-stranded")
    assert after is not None
    assert after.status == JOB_STATUS_COMPLETED
    # The run incremented the attempt counter above the stranded value.
    assert after.attempt == 2

    # The JSONL preserved the original queued + started events and
    # appended two new events: a fresh started (resumed) and a completed.
    parsed = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    events = [row["event"] for row in parsed]
    assert events == [
        JOB_EVENT_QUEUED,
        JOB_EVENT_STARTED,
        JOB_EVENT_STARTED,  # resumed
        JOB_EVENT_COMPLETED,
    ]
    # The resumed started event must carry attempt 2 and the resumed
    # marker so operators can grep for crash recoveries.
    resumed_event = parsed[2]
    assert resumed_event["event"] == JOB_EVENT_STARTED
    assert resumed_event["attempt"] == 2
    assert resumed_event["attempt_metadata"]["number"] == 2
    assert resumed_event["attempt_metadata"].get("resumed") is True


# ---- WU-009 unknown future event replay -----------------------------
#
# A future version of the queue may emit event types this implementation
# does not know about. The contract is that ``JobEvent.from_dict`` must
# reject unknown types (raising ``JobError``) and that
# ``_read_jsonl_events`` must continue skipping those lines so they
# cannot mutate the materialized view of an already-terminal job.


def _write_event(
    path: Path,
    *,
    seq: int,
    event: str,
    job_id: str,
    status: str,
    created_at: str,
    attempt: int = 0,
    spec: dict[str, Any] | None = None,
    **extra: Any,
) -> None:
    """Append a single schema 1.0.0 JSONL event line to ``path``."""
    payload = {
        "schema_version": JOB_SCHEMA_VERSION,
        "seq": seq,
        "event": event,
        "id": job_id,
        "job_id": job_id,
        "created_at": created_at,
        "status": status,
        "spec": spec or {"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        "attempt": attempt,
        "attempt_metadata": {"number": attempt},
    }
    payload.update(extra)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


def test_unknown_future_event_after_cancelled_does_not_resurrect_job(tmp_path):
    """A JSONL containing queued → cancelled → unknown future event for the
    same id must replay as ``cancelled``.

    The unknown future event carries ``status=running`` and the same
    ``job_id`` as the already-cancelled job. Without the unknown-event
    rejection the replay path would apply it and the materialized job
    would flip back to ``running``, resurrecting a terminal job. With the
    rejection in place, ``_read_jsonl_events`` silently drops the unknown
    line and the job stays terminal.
    """
    path = tmp_path / "jobs.jsonl"
    _write_event(
        path,
        seq=1,
        event=JOB_EVENT_QUEUED,
        job_id="job-future",
        status=JOB_STATUS_PENDING,
        created_at="2026-06-19T12:00:00Z",
    )
    _write_event(
        path,
        seq=2,
        event=JOB_EVENT_CANCELLED,
        job_id="job-future",
        status=JOB_STATUS_CANCELLED,
        created_at="2026-06-19T12:00:01Z",
    )
    # Future event type this implementation does not know about. If it
    # were applied, the job would flip to ``running``.
    _write_event(
        path,
        seq=3,
        event="resumed-by-future-writer",
        job_id="job-future",
        status=JOB_STATUS_RUNNING,
        created_at="2026-06-19T12:00:02Z",
        attempt=2,
    )

    store = JobStore(path, clock=_fixed_clock())

    replayed = store.get("job-future")
    assert replayed is not None
    # Materialized status must still be cancelled — the unknown event did
    # not resurrect the job.
    assert replayed.status == JOB_STATUS_CANCELLED
    assert replayed.is_terminal is True
    # The materialized events tuple only carries the two known events;
    # the unknown future event is dropped.
    observed_events = [ev.event for ev in replayed.events]
    assert observed_events == [JOB_EVENT_QUEUED, JOB_EVENT_CANCELLED]
    # The job is no longer pending and therefore not retryable.
    assert store.pending() == []
    # ``store.jobs()`` still returns the job in FIFO order with the
    # terminal status preserved.
    assert [job.status for job in store.jobs()] == [JOB_STATUS_CANCELLED]


def test_unknown_event_is_skipped_from_materialized_events(tmp_path):
    """An unknown event line must not appear in ``store.events()``.

    The skipped line must not affect FIFO ordering, status, attempt
    counter, attempt_metadata, or retryability of any job in the log.
    """
    path = tmp_path / "jobs.jsonl"
    # Job A: queued → cancelled (terminal), then an unknown event in
    # between the two normal events to confirm the unknown line is
    # silently dropped mid-log without breaking the replay.
    _write_event(
        path,
        seq=1,
        event=JOB_EVENT_QUEUED,
        job_id="job-a",
        status=JOB_STATUS_PENDING,
        created_at="2026-06-19T12:00:00Z",
    )
    _write_event(
        path,
        seq=2,
        event="future-only-state",
        job_id="job-a",
        status=JOB_STATUS_RUNNING,
        created_at="2026-06-19T12:00:01Z",
        attempt=1,
    )
    _write_event(
        path,
        seq=3,
        event=JOB_EVENT_CANCELLED,
        job_id="job-a",
        status=JOB_STATUS_CANCELLED,
        created_at="2026-06-19T12:00:02Z",
    )
    # Job B: a separate job with an unknown event appended at the end.
    _write_event(
        path,
        seq=4,
        event=JOB_EVENT_QUEUED,
        job_id="job-b",
        status=JOB_STATUS_PENDING,
        created_at="2026-06-19T12:00:03Z",
    )
    _write_event(
        path,
        seq=5,
        event="checkpoint-future",
        job_id="job-b",
        status=JOB_STATUS_RUNNING,
        created_at="2026-06-19T12:00:04Z",
        attempt=2,
    )

    store = JobStore(path, clock=_fixed_clock())

    # The materialized events list contains no unknown event types.
    materialized_event_types = [ev.event for ev in store.events()]
    assert materialized_event_types == [
        JOB_EVENT_QUEUED,
        JOB_EVENT_CANCELLED,
        JOB_EVENT_QUEUED,
    ]
    # Job A: cancelled, no unknown event affected its attempt/status.
    job_a = store.get("job-a")
    assert job_a is not None
    assert job_a.status == JOB_STATUS_CANCELLED
    assert job_a.attempt == 0
    assert job_a.attempt_metadata.get("number") == 0
    # Job B: still pending (queued) because the unknown event with
    # status=running was skipped; its attempt counter is unchanged.
    job_b = store.get("job-b")
    assert job_b is not None
    assert job_b.status == JOB_STATUS_PENDING
    assert job_b.attempt == 0
    # FIFO order preserved: job-a before job-b.
    assert [job.job_id for job in store.jobs()] == ["job-a", "job-b"]
    # Only job-b is retryable.
    pending_ids = [job.job_id for job in store.pending()]
    assert pending_ids == ["job-b"]


def test_malformed_trailing_line_remains_non_fatal(tmp_path):
    """A malformed trailing JSONL line must not abort replay.

    The contract is that ``_read_jsonl_events`` silently skips lines
    that fail ``json.loads`` (existing behaviour) and lines that raise
    ``JobError`` during ``JobEvent.from_dict`` (now also true for
    unknown event types). The store must continue past such lines and
    expose only the well-formed events.
    """
    path = tmp_path / "jobs.jsonl"
    _write_event(
        path,
        seq=1,
        event=JOB_EVENT_QUEUED,
        job_id="job-good-1",
        status=JOB_STATUS_PENDING,
        created_at="2026-06-19T12:00:00Z",
    )
    _write_event(
        path,
        seq=2,
        event=JOB_EVENT_CANCELLED,
        job_id="job-good-1",
        status=JOB_STATUS_CANCELLED,
        created_at="2026-06-19T12:00:01Z",
    )
    # Trailing garbage: not JSON.
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
        fh.write("\n")
        # Unknown event type — a fresh ``JobError`` path.
        fh.write(
            json.dumps(
                {
                    "schema_version": JOB_SCHEMA_VERSION,
                    "seq": 3,
                    "event": "resumed-by-future-writer",
                    "id": "job-good-1",
                    "job_id": "job-good-1",
                    "created_at": "2026-06-19T12:00:02Z",
                    "status": JOB_STATUS_RUNNING,
                    "spec": {},
                    "attempt": 3,
                    "attempt_metadata": {"number": 3},
                }
            )
            + "\n"
        )
        # Another well-formed event after the trailing garbage so the
        # store does not stop scanning on first error.
        fh.write(
            json.dumps(
                {
                    "schema_version": JOB_SCHEMA_VERSION,
                    "seq": 4,
                    "event": JOB_EVENT_QUEUED,
                    "id": "job-good-2",
                    "job_id": "job-good-2",
                    "created_at": "2026-06-19T12:00:03Z",
                    "status": JOB_STATUS_PENDING,
                    "spec": {"kind": "recipe", "recipe": "pr-review"},
                    "attempt": 0,
                    "attempt_metadata": {"number": 0},
                }
            )
            + "\n"
        )

    store = JobStore(path, clock=_fixed_clock())

    # The store did not abort: both well-formed jobs are visible and
    # job-good-1 is still cancelled (the unknown event did not
    # resurrect it).
    assert [job.job_id for job in store.jobs()] == ["job-good-1", "job-good-2"]
    job_one = store.get("job-good-1")
    assert job_one is not None and job_one.status == JOB_STATUS_CANCELLED
    # No unknown event type leaked into the materialized event stream.
    materialized = [ev.event for ev in store.events()]
    assert "resumed-by-future-writer" not in materialized
    assert materialized == [
        JOB_EVENT_QUEUED,
        JOB_EVENT_CANCELLED,
        JOB_EVENT_QUEUED,
    ]


def test_job_event_from_dict_rejects_unknown_event_type_directly():
    """``JobEvent.from_dict`` must raise ``JobError`` on an unknown event.

    This pins the *direct* rejection (not just the replay-time skip) so
    any caller using ``from_dict`` outside the JSONL path is protected
    from accidentally materialising future event types.
    """
    payload = {
        "schema_version": JOB_SCHEMA_VERSION,
        "seq": 1,
        "event": "future-event-type",
        "id": "job-xyz",
        "job_id": "job-xyz",
        "created_at": "2026-06-19T12:00:00Z",
        "status": JOB_STATUS_RUNNING,
        "spec": {"kind": "recipe", "recipe": "pr-review"},
        "attempt": 1,
        "attempt_metadata": {"number": 1},
    }
    with pytest.raises(JobError) as exc_info:
        JobEvent.from_dict(payload)
    assert "future-event-type" in str(exc_info.value)


def test_known_event_types_constant_is_complete():
    """The ``JOB_KNOWN_EVENT_TYPES`` constant must enumerate every WU-009
    event type this implementation supports.

    This guards against a silent regression where a new event type is
    added (e.g. ``paused``) but the ``JOB_KNOWN_EVENT_TYPES`` constant is
    forgotten, which would silently accept the event for replay while
    leaving the rest of the code unaware of it.
    """
    assert JOB_KNOWN_EVENT_TYPES == frozenset(
        {
            JOB_EVENT_QUEUED,
            JOB_EVENT_STARTED,
            JOB_EVENT_COMPLETED,
            JOB_EVENT_FAILED,
            JOB_EVENT_CANCELLED,
        }
    )


# ---- WU-009 cancellation terminality fixes -------------------------


def test_cancelled_job_rejects_later_completed_on_replay(tmp_path):
    """queued → started → cancelled → completed must replay as cancelled.

    A cancellation tombstone is terminal. Later known events in the append-
    only log must be tolerated but must not resurrect the materialized status,
    attempt metadata, retryability, or terminal state.
    """
    path = tmp_path / "jobs.jsonl"
    _write_event(
        path,
        seq=1,
        event=JOB_EVENT_QUEUED,
        job_id="job-x",
        status=JOB_STATUS_PENDING,
        created_at="2026-06-19T12:00:00Z",
        attempt=0,
    )
    _write_event(
        path,
        seq=2,
        event=JOB_EVENT_STARTED,
        job_id="job-x",
        status=JOB_STATUS_RUNNING,
        created_at="2026-06-19T12:00:01Z",
        attempt=1,
    )
    _write_event(
        path,
        seq=3,
        event=JOB_EVENT_CANCELLED,
        job_id="job-x",
        status=JOB_STATUS_CANCELLED,
        created_at="2026-06-19T12:00:02Z",
        attempt=1,
    )
    _write_event(
        path,
        seq=4,
        event=JOB_EVENT_COMPLETED,
        job_id="job-x",
        status=JOB_STATUS_COMPLETED,
        created_at="2026-06-19T12:00:03Z",
        attempt=1,
        spec={"kind": "recipe", "recipe": "pr-review"},
    )

    store = JobStore(path, clock=_fixed_clock())
    replayed = store.get("job-x")
    assert replayed is not None
    assert replayed.status == JOB_STATUS_CANCELLED
    assert replayed.is_terminal is True
    assert replayed.attempt == 1
    assert replayed.attempt_metadata.get("number") == 1
    assert "job-x" not in {job.job_id for job in store.pending()}


def test_cancelled_job_rejects_later_failed_on_replay(tmp_path):
    """queued → started → cancelled → failed must replay as cancelled.

    The later ``failed`` event must not replace the tombstone status and
    its error must not leak into ``last_error``.
    """
    path = tmp_path / "jobs.jsonl"
    _write_event(
        path,
        seq=1,
        event=JOB_EVENT_QUEUED,
        job_id="job-y",
        status=JOB_STATUS_PENDING,
        created_at="2026-06-19T12:00:00Z",
        attempt=0,
    )
    _write_event(
        path,
        seq=2,
        event=JOB_EVENT_STARTED,
        job_id="job-y",
        status=JOB_STATUS_RUNNING,
        created_at="2026-06-19T12:00:01Z",
        attempt=1,
    )
    _write_event(
        path,
        seq=3,
        event=JOB_EVENT_CANCELLED,
        job_id="job-y",
        status=JOB_STATUS_CANCELLED,
        created_at="2026-06-19T12:00:02Z",
        attempt=1,
    )
    _write_event(
        path,
        seq=4,
        event=JOB_EVENT_FAILED,
        job_id="job-y",
        status=JOB_STATUS_FAILED,
        created_at="2026-06-19T12:00:03Z",
        attempt=1,
    )

    store = JobStore(path, clock=_fixed_clock())
    replayed = store.get("job-y")
    assert replayed is not None
    assert replayed.status == JOB_STATUS_CANCELLED
    assert replayed.is_terminal is True
    assert replayed.last_error is None
    assert "later failure" not in (replayed.last_error or "")
    assert "job-y" not in {job.job_id for job in store.pending()}


def test_recipe_job_cancelled_during_execution_does_not_complete(tmp_path):
    """If a provider cancels a job after ``started`` is appended, the runner
    must record it as cancelled and must not append ``completed``.
    """
    store = _store(tmp_path)
    job = store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        )
    )

    class _CancelThenReturnRecipeModule:
        def get_recipe(self, _name):
            return _StubCancelRecipe()

        def collect_recipe_input(self, _recipe, *, prompt, stdin, input_file, path):
            return prompt or "stub-input", path

        def run_recipe(self, _pool, _recipe, *, input_text, path, **kwargs):
            # Simulate a race: cancellation arrives while the provider is
            # busy, after the runner has already appended ``started``.
            store.cancel(job.job_id)
            return _FakeRecipeRun(output="ran-after-cancel")

        def write_recipe_record(self, run, *, store=None):
            s = store or RunRecordStore()
            return s.append_new(
                kind="recipe",
                title="t",
                prompt=run.prompt,
                output=run.output,
                recipe="pr-review",
            )

    class _StubCancelRecipe:
        name = "pr-review"
        version = "1.0.0"
        role = "critic"

    import freellmpool.jobs as jobs_mod

    jobs_mod.write_report = lambda *a, **k: Path("/dev/null")

    outcome = run_pending_jobs(
        store,
        pool_factory=_pool_factory(),
        recipes_module=_CancelThenReturnRecipeModule(),
        record_store=RunRecordStore(tmp_path / "records.jsonl"),
    )

    assert {j.job_id for j in outcome.cancelled} == {job.job_id}
    job_events = [e for e in store.events() if e.job_id == job.job_id]
    assert [e.event for e in job_events] == [
        JOB_EVENT_QUEUED,
        JOB_EVENT_STARTED,
        JOB_EVENT_CANCELLED,
    ]


def test_recipe_job_cancelled_before_exception_does_not_fail(tmp_path):
    """If a provider cancels a job and then raises, the runner must record
    it as cancelled and must not append ``failed``.
    """
    store = _store(tmp_path)
    job = store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        )
    )

    class _CancelThenRaiseRecipeModule:
        def get_recipe(self, _name):
            return _StubCancelRecipe()

        def collect_recipe_input(self, _recipe, *, prompt, stdin, input_file, path):
            return prompt or "stub-input", path

        def run_recipe(self, _pool, _recipe, *, input_text, path, **kwargs):
            store.cancel(job.job_id)
            raise RuntimeError("provider exploded after cancel")

        def write_recipe_record(self, run, *, store=None):
            s = store or RunRecordStore()
            return s.append_new(
                kind="recipe",
                title="t",
                prompt=run.prompt,
                output=run.output,
                recipe="pr-review",
            )

    class _StubCancelRecipe:
        name = "pr-review"
        version = "1.0.0"
        role = "critic"

    import freellmpool.jobs as jobs_mod

    jobs_mod.write_report = lambda *a, **k: Path("/dev/null")

    outcome = run_pending_jobs(
        store,
        pool_factory=_pool_factory(),
        recipes_module=_CancelThenRaiseRecipeModule(),
        record_store=RunRecordStore(tmp_path / "records.jsonl"),
    )

    assert {j.job_id for j in outcome.cancelled} == {job.job_id}
    assert not outcome.failed
    job_events = [e for e in store.events() if e.job_id == job.job_id]
    assert [e.event for e in job_events] == [
        JOB_EVENT_QUEUED,
        JOB_EVENT_STARTED,
        JOB_EVENT_CANCELLED,
    ]
    after = store.get(job.job_id)
    assert after is not None
    assert after.status == JOB_STATUS_CANCELLED
    assert after.last_error is None


# ---- WU-009 terminal replay freeze for completed/failed ------------


def test_completed_job_rejects_later_started_and_stays_terminal(tmp_path):
    """queued → completed → started must replay as completed.

    A later known event must not overwrite the first terminal status or the
    terminal side effects (output, provider_id, model, run_id, attempt).
    """
    path = tmp_path / "jobs.jsonl"
    spec: dict[str, Any] = {"kind": "recipe", "recipe": "pr-review", "prompt": "x"}
    _write_event(
        path,
        seq=1,
        event=JOB_EVENT_QUEUED,
        job_id="job-complete",
        status=JOB_STATUS_PENDING,
        created_at="2026-06-19T12:00:00Z",
        attempt=0,
        spec=spec,
    )
    _write_event(
        path,
        seq=2,
        event=JOB_EVENT_COMPLETED,
        job_id="job-complete",
        status=JOB_STATUS_COMPLETED,
        created_at="2026-06-19T12:00:01Z",
        attempt=1,
        spec=spec,
        output="final output",
        run_id="run-abc",
        provider_id="openai",
        model="gpt-4",
    )
    _write_event(
        path,
        seq=3,
        event=JOB_EVENT_STARTED,
        job_id="job-complete",
        status=JOB_STATUS_RUNNING,
        created_at="2026-06-19T12:00:02Z",
        attempt=2,
        spec=spec,
    )

    store = JobStore(path, clock=_fixed_clock())
    replayed = store.get("job-complete")
    assert replayed is not None
    assert replayed.status == JOB_STATUS_COMPLETED
    assert replayed.is_terminal is True
    assert replayed.output == "final output"
    assert replayed.run_id == "run-abc"
    assert replayed.provider_id == "openai"
    assert replayed.model == "gpt-4"
    assert replayed.attempt == 1
    assert replayed.attempt_metadata.get("number") == 1
    assert "job-complete" not in {job.job_id for job in store.pending()}
    # The later started event is still preserved in event history.
    assert [e.event for e in replayed.events] == [
        JOB_EVENT_QUEUED,
        JOB_EVENT_COMPLETED,
        JOB_EVENT_STARTED,
    ]


def test_failed_job_rejects_later_started_and_completed(tmp_path):
    """queued → failed → started → completed must replay as failed.

    The first failure error and terminality must be preserved even when
    subsequent known events would otherwise overwrite them.
    """
    path = tmp_path / "jobs.jsonl"
    spec: dict[str, Any] = {"kind": "recipe", "recipe": "pr-review", "prompt": "x"}
    _write_event(
        path,
        seq=1,
        event=JOB_EVENT_QUEUED,
        job_id="job-fail",
        status=JOB_STATUS_PENDING,
        created_at="2026-06-19T12:00:00Z",
        attempt=0,
        spec=spec,
    )
    _write_event(
        path,
        seq=2,
        event=JOB_EVENT_FAILED,
        job_id="job-fail",
        status=JOB_STATUS_FAILED,
        created_at="2026-06-19T12:00:01Z",
        attempt=1,
        spec=spec,
        error="first failure",
    )
    _write_event(
        path,
        seq=3,
        event=JOB_EVENT_STARTED,
        job_id="job-fail",
        status=JOB_STATUS_RUNNING,
        created_at="2026-06-19T12:00:02Z",
        attempt=2,
        spec=spec,
    )
    _write_event(
        path,
        seq=4,
        event=JOB_EVENT_COMPLETED,
        job_id="job-fail",
        status=JOB_STATUS_COMPLETED,
        created_at="2026-06-19T12:00:03Z",
        attempt=2,
        spec=spec,
        output="later success",
    )

    store = JobStore(path, clock=_fixed_clock())
    replayed = store.get("job-fail")
    assert replayed is not None
    assert replayed.status == JOB_STATUS_FAILED
    assert replayed.is_terminal is True
    assert replayed.last_error == "first failure"
    assert "later success" != replayed.output
    assert replayed.attempt == 1
    assert replayed.attempt_metadata.get("number") == 1
    assert "job-fail" not in {job.job_id for job in store.pending()}
    assert [e.event for e in replayed.events] == [
        JOB_EVENT_QUEUED,
        JOB_EVENT_FAILED,
        JOB_EVENT_STARTED,
        JOB_EVENT_COMPLETED,
    ]


# ---- WU-009 ask audit metadata -------------------------------------


def test_ask_job_completion_preserves_reply_provider_and_model(tmp_path):
    """An ask-job ``completed`` event must preserve provider_id and model."""
    pool = _FakePool(["hello world"])
    store = _store(tmp_path)
    job = store.add(
        JobSpec(
            kind=JOB_KIND_ASK,
            payload={"kind": "ask", "role": "summarizer", "prompt": "x"},
        )
    )

    outcome = run_pending_jobs(store, pool_factory=_pool_factory(pool))

    assert len(outcome.completed) == 1
    after = store.get(job.job_id)
    assert after is not None
    assert after.status == JOB_STATUS_COMPLETED
    assert after.output == "hello world"
    assert after.provider_id == "fake"
    assert after.model == "critic"

    completed_event = next(
        event
        for event in store.events()
        if event.event == JOB_EVENT_COMPLETED and event.job_id == job.job_id
    )
    assert completed_event.output == "hello world"
    assert completed_event.provider_id == "fake"
    assert completed_event.model == "critic"


def test_ask_job_race_with_terminal_event_does_not_append_second_terminal(tmp_path):
    """If a terminal event appears during ask execution, the runner must not
    append a second terminal event for the same job.
    """
    store = _store(tmp_path)
    job = store.add(
        JobSpec(
            kind=JOB_KIND_ASK,
            payload={"kind": "ask", "role": "summarizer", "prompt": "x"},
        )
    )

    class _RacePool(_FakePool):
        def __init__(self, store: JobStore, job_id: str) -> None:
            super().__init__([])
            self._store = store
            self._job_id = job_id

        def ask(self, prompt, **kwargs):
            # Simulate another runner finishing the job while we are busy.
            spec = self._store.get(self._job_id).spec
            self._store._append_event_locked(
                job_id=self._job_id,
                event_type=JOB_EVENT_COMPLETED,
                status=JOB_STATUS_COMPLETED,
                spec=spec,
                attempt=1,
                attempt_metadata={"number": 1},
                output="race-winner",
                provider_id="race-provider",
                model="race-model",
            )
            return Reply(text="late", provider_id="fake", model="critic", raw={})

    outcome = run_pending_jobs(
        store, pool_factory=_pool_factory(_RacePool(store, job.job_id))
    )

    job_events = [e for e in store.events() if e.job_id == job.job_id]
    assert [e.event for e in job_events] == [
        JOB_EVENT_QUEUED,
        JOB_EVENT_STARTED,
        JOB_EVENT_COMPLETED,
    ]
    final = store.get(job.job_id)
    assert final is not None
    assert final.status == JOB_STATUS_COMPLETED
    # The materialized output is from the first terminal event, not the late reply.
    assert final.output == "race-winner"
    assert final.provider_id == "race-provider"
    assert final.model == "race-model"
    # The runner observed a completion outcome without appending again.
    assert len(outcome.completed) == 1


# ---- WU-009 append after partial trailing JSONL --------------------
#
# Regression coverage for the append-safety contract: if the JSONL ends
# with a malformed partial line that has no trailing newline (e.g. a
# crashed previous writer), the next append must start the new event on
# its own JSONL line so replay can find it. Without the fix the new event
# would be glued to the partial fragment and silently lost on replay.


def test_add_after_partial_trailing_fragment_is_materialised(tmp_path):
    """``store.add`` after a malformed trailing fragment must materialise
    the new job.

    The JSONL is seeded with one well-formed event followed by a partial
    malformed fragment that has no trailing newline. The subsequent
    ``store.add`` call must still return a materialized job visible in
    ``store.jobs()`` / ``store.events()``.
    """
    path = tmp_path / "jobs.jsonl"
    # First well-formed event so the file is not empty when seeded.
    _write_event(
        path,
        seq=1,
        event=JOB_EVENT_QUEUED,
        job_id="job-seed",
        status=JOB_STATUS_PENDING,
        created_at="2026-06-19T12:00:00Z",
    )
    # Append a partial malformed JSON fragment WITHOUT a trailing newline
    # so the file ends mid-line. The next append must insert a newline
    # before writing the new event.
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json")

    store = JobStore(path, clock=_fixed_clock())
    added = store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        )
    )
    assert added.status == JOB_STATUS_PENDING

    # The new job must be visible in jobs()/events().
    job_ids = {job.job_id for job in store.jobs()}
    assert added.job_id in job_ids
    event_job_ids = {event.job_id for event in store.events()}
    assert added.job_id in event_job_ids


def test_add_after_partial_trailing_fragment_separates_jsonl_lines(tmp_path):
    """The new event must land on its own JSONL line after the malformed
    fragment and parse as JSON.

    The raw bytes must contain the original malformed fragment on its own
    line (after the inserted newline) followed by the new event line that
    parses cleanly via ``json.loads``.
    """
    path = tmp_path / "jobs.jsonl"
    _write_event(
        path,
        seq=1,
        event=JOB_EVENT_QUEUED,
        job_id="job-seed",
        status=JOB_STATUS_PENDING,
        created_at="2026-06-19T12:00:00Z",
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json")

    store = JobStore(path, clock=_fixed_clock())
    store.add(
        JobSpec(
            kind=JOB_KIND_RECIPE,
            payload={"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
        )
    )

    raw_bytes = path.read_bytes()
    # The malformed fragment is preserved byte-for-byte (append-only) and
    # is now followed by a newline because the writer inserted one before
    # the new event.
    assert b"{not json" in raw_bytes
    assert b"{not json\n" in raw_bytes, (
        "malformed fragment must be left untouched but separated onto its "
        "own line"
    )

    # Splitting on newlines yields the well-formed seed line, the
    # malformed fragment on its own line, and the new valid event line.
    raw_text = raw_bytes.decode("utf-8")
    lines = raw_text.split("\n")
    # Drop the trailing empty string produced by the final newline so we
    # only inspect actual record lines.
    record_lines = [line for line in lines if line != ""]
    assert len(record_lines) == 3, f"expected 3 record lines, got {record_lines!r}"

    malformed_line = record_lines[1]
    assert malformed_line == "{not json", (
        "the malformed fragment must appear unchanged on its own line"
    )

    new_event_line = record_lines[2]
    parsed = json.loads(new_event_line)
    assert parsed["event"] == JOB_EVENT_QUEUED
    assert parsed["status"] == JOB_STATUS_PENDING
    assert parsed["spec"]["recipe"] == "pr-review"


def test_run_after_partial_trailing_fragment_appends_valid_event(tmp_path):
    """A later run/cancel after a partial trailing fragment must append
    a valid event line and replay normally.

    The runner appends ``started`` / ``completed`` events after the
    fragment. Both must land on clean lines and be visible on replay.
    """
    path = tmp_path / "jobs.jsonl"
    _write_event(
        path,
        seq=1,
        event=JOB_EVENT_QUEUED,
        job_id="job-runnable",
        status=JOB_STATUS_PENDING,
        created_at="2026-06-19T12:00:00Z",
        spec={"kind": "recipe", "recipe": "pr-review", "prompt": "x"},
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json")

    pool = _FakePool(["ok"])
    recipes = _fake_recipes_module()

    import freellmpool.jobs as jobs_mod

    jobs_mod.write_report = lambda *a, **k: Path("/dev/null")

    store = JobStore(path, clock=_fixed_clock())
    # Sanity: the well-formed queued event is replayable even with the
    # trailing partial fragment present.
    replayed_before = store.get("job-runnable")
    assert replayed_before is not None
    assert replayed_before.status == JOB_STATUS_PENDING

    outcome = run_pending_jobs(
        store,
        pool_factory=_pool_factory(pool),
        recipes_module=recipes,
    )
    assert len(outcome.completed) == 1

    after = store.get("job-runnable")
    assert after is not None
    assert after.status == JOB_STATUS_COMPLETED

    # Every line in the final file (after splitting on newlines and
    # dropping the trailing empty string) must be either the malformed
    # fragment, or parseable JSON.
    raw_text = path.read_text(encoding="utf-8")
    raw_lines = [line for line in raw_text.split("\n") if line != ""]
    assert len(raw_lines) == 4, f"expected 4 lines, got {raw_lines!r}"
    assert raw_lines[1] == "{not json", (
        "malformed fragment must remain on its own line, unchanged"
    )
    parsed = [json.loads(line) for line in raw_lines if line != "{not json"]
    events = [row["event"] for row in parsed]
    assert events == [JOB_EVENT_QUEUED, JOB_EVENT_STARTED, JOB_EVENT_COMPLETED]
