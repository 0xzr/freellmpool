from __future__ import annotations

import importlib.util
import json
import re
import socketserver
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from freellmpool.errors import ProviderHTTPError
from freellmpool.models import Model, Provider, Reply

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "catalog_sentinel",
    ROOT / "scripts" / "catalog_sentinel.py",
)
assert SPEC is not None and SPEC.loader is not None
SENTINEL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SENTINEL
SPEC.loader.exec_module(SENTINEL)


def _provider() -> Provider:
    return Provider(
        id="example",
        label="Example",
        adapter="openai",
        base_url="https://example.test/v1",
        key_env="EXAMPLE_KEY",
        models=(
            Model("kept", enabled=True),
            Model("missing", enabled=True),
        ),
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, "ok"),
        (401, "auth_required"),
        (402, "billing_or_credit"),
        (403, "auth_required"),
        (404, "listing_unsupported"),
        (429, "rate_limited"),
        (500, "transient_provider_error"),
    ],
)
def test_http_failures_are_classified_without_retirement(status, expected):
    assert SENTINEL.classify_http(status) == expected


def test_model_listing_normalization_is_bounded_and_strict():
    payload = {
        "data": [
            {"id": "model-a"},
            {"id": "model-a"},
            {"id": "model/b:free"},
            {"id": "bad\nmodel"},
            {"id": "bad`model"},
            {"id": "<tag>"},
            {"id": "x" * 201},
            {"wrong": "ignored"},
        ]
    }

    assert SENTINEL.normalize_model_listing(payload) == (
        "model-a",
        "model/b:free",
    )
    assert SENTINEL.normalize_model_listing({"data": "not-a-list"}) == ()
    assert SENTINEL.normalize_model_listing(["model-c", {"id": "model-d"}]) == (
        "model-c",
        "model-d",
    )


def test_aion_top_level_models_listing_is_normalized():
    assert SENTINEL.normalize_model_listing(
        {"models": [{"id": "aion-labs/aion-3.0"}, {"name": "aion-labs/aion-3.0-mini"}]}
    ) == ("aion-labs/aion-3.0", "aion-labs/aion-3.0-mini")


def test_pollinations_listing_normalizes_name_and_aliases():
    payload = [
        {
            "name": "openai-fast",
            "aliases": [
                "openai",
                "gpt-oss",
                "bad`alias",
                "x" * 201,
                42,
            ],
        }
    ]

    assert SENTINEL.normalize_model_listing(payload) == (
        "gpt-oss",
        "openai",
        "openai-fast",
    )


def test_transient_or_incomplete_listing_never_recommends_retirement():
    now = datetime(2026, 7, 29, 12, tzinfo=UTC)
    provider = _provider()

    transient = SENTINEL.discovery_record(
        provider,
        status=429,
        payload={},
        observed_at=now,
    )
    incomplete = SENTINEL.discovery_record(
        provider,
        status=200,
        payload={"data": []},
        observed_at=now,
    )

    assert transient["failure_classification"] == "rate_limited"
    assert transient["removed_models"] == []
    assert transient["retirement_candidates"] == []
    assert incomplete["listing_complete"] is False
    assert incomplete["failure_classification"] == "invalid_or_empty_listing"
    assert incomplete["removed_models"] == []
    assert incomplete["retirement_candidates"] == []


def test_successful_listing_reports_drift_but_never_mutates_catalog():
    now = datetime(2026, 7, 29, 12, tzinfo=UTC)
    provider = _provider()

    record = SENTINEL.discovery_record(
        provider,
        status=200,
        payload={"data": [{"id": "kept"}, {"id": "new-model"}]},
        observed_at=now,
        listing_authoritative=True,
    )

    assert record["new_models"] == ["new-model"]
    assert record["removed_models"] == ["missing"]
    assert record["retirement_candidates"] == []
    assert record["advisory_only"] is True
    assert record["last_discovered"] == "2026-07-29T12:00:00Z"
    assert record["verification_count"] == 0
    assert record["free_tier_kind"] == "unknown"
    assert record["billing_risk"] == "review_required"


def test_partial_listing_reports_unconfirmed_absence_not_removal():
    now = datetime(2026, 7, 29, 12, tzinfo=UTC)
    provider = _provider()

    record = SENTINEL.discovery_record(
        provider,
        status=200,
        payload={"data": [{"id": "kept"}, {"id": "new-model"}]},
        observed_at=now,
        listing_authoritative=False,
    )

    assert record["listing_complete"] is False
    assert record["listing_scope"] == "partial_or_unknown"
    assert record["new_models"] == ["new-model"]
    assert record["removed_models"] == []
    assert record["unconfirmed_absences"] == ["missing"]
    assert record["retirement_candidates"] == []


def test_discovery_lifecycle_merges_only_matching_previous_records():
    provider = _provider()
    previous = {
        "schema_version": 1,
        "mode": "public_discovery",
        "providers": [
            {
                "provider": "example",
                "first_discovered": "2026-07-22T12:00:00Z",
                "last_discovered": "2026-07-22T12:00:00Z",
                "discovery_count": 4,
            },
            {
                "provider": "other",
                "first_discovered": "secret-account-id",
                "discovery_count": 999,
            },
        ],
    }

    report = SENTINEL.discover(
        [provider],
        timeout=1,
        max_bytes=1024,
        now=datetime(2026, 7, 29, 12, tzinfo=UTC),
        previous=previous,
        fetch=lambda _url, **_kwargs: (200, {"data": [{"id": "kept"}]}),
    )

    record = report["providers"][0]
    assert record["first_discovered"] == "2026-07-22T12:00:00Z"
    assert record["last_discovered"] == "2026-07-29T12:00:00Z"
    assert record["discovery_count"] == 5
    assert record["baseline_initialized"] is True
    assert record["new_models"] == []
    assert record["catalog_gaps"] == []
    assert "secret-account-id" not in json.dumps(report)


def test_discovery_alerts_on_changes_after_baseline_not_all_catalog_gaps():
    provider = _provider()
    first = SENTINEL.discover(
        [provider],
        timeout=1,
        max_bytes=1024,
        now=datetime(2026, 7, 22, 12, tzinfo=UTC),
        fetch=lambda _url, **_kwargs: (
            200,
            {"data": [{"id": "kept"}, {"id": "candidate-a"}]},
        ),
    )

    assert first["drift"]["has_changes"] is False
    assert first["providers"][0]["new_models"] == []
    assert first["providers"][0]["catalog_gaps"] == ["candidate-a"]
    assert first["providers"][0]["observed_models"] == ["candidate-a", "kept"]

    second = SENTINEL.discover(
        [provider],
        timeout=1,
        max_bytes=1024,
        now=datetime(2026, 7, 29, 12, tzinfo=UTC),
        previous=first,
        fetch=lambda _url, **_kwargs: (
            200,
            {"data": [{"id": "kept"}, {"id": "candidate-a"}, {"id": "candidate-b"}]},
        ),
    )

    assert second["drift"]["has_changes"] is True
    assert second["providers"][0]["new_models"] == ["candidate-b"]
    assert second["providers"][0]["catalog_gaps"] == [
        "candidate-a",
        "candidate-b",
    ]


def test_authoritative_first_baseline_reports_catalog_additions_and_removals():
    base = _provider()
    provider = Provider(
        id="pollinations",
        label=base.label,
        adapter=base.adapter,
        base_url=base.base_url,
        key_env=base.key_env,
        models=base.models,
    )

    report = SENTINEL.discover(
        [provider],
        timeout=1,
        max_bytes=1024,
        now=datetime(2026, 7, 29, 12, tzinfo=UTC),
        fetch=lambda _url, **_kwargs: (
            200,
            [{"name": "kept", "aliases": ["new-model"]}],
        ),
    )

    row = report["providers"][0]
    assert row["listing_complete"] is True
    assert row["new_models"] == ["new-model"]
    assert row["removed_models"] == ["missing"]
    assert report["drift"]["has_changes"] is True


def test_partial_listing_tracks_repeated_absence_and_recovery_without_retirement():
    provider = _provider()
    baseline = {
        "schema_version": 1,
        "mode": "public_discovery",
        "providers": [
            {
                "provider": "example",
                "observed_models": ["kept", "candidate-a"],
                "absence_streaks": {},
            }
        ],
    }

    first_absence = SENTINEL.discover(
        [provider],
        timeout=1,
        max_bytes=1024,
        now=datetime(2026, 7, 22, 12, tzinfo=UTC),
        previous=baseline,
        fetch=lambda _url, **_kwargs: (200, {"data": [{"id": "kept"}]}),
    )
    row = first_absence["providers"][0]
    assert row["removed_models"] == []
    assert row["unconfirmed_absences"] == ["candidate-a"]
    assert row["absence_streaks"] == {"candidate-a": 1}
    assert row["repeated_absences"] == []
    assert first_absence["drift"]["has_changes"] is False

    second_absence = SENTINEL.discover(
        [provider],
        timeout=1,
        max_bytes=1024,
        now=datetime(2026, 7, 29, 12, tzinfo=UTC),
        previous=first_absence,
        fetch=lambda _url, **_kwargs: (200, {"data": [{"id": "kept"}]}),
    )
    row = second_absence["providers"][0]
    assert row["absence_streaks"] == {"candidate-a": 2}
    assert row["repeated_absences"] == ["candidate-a"]
    assert second_absence["drift"]["has_changes"] is True
    assert row["retirement_candidates"] == []

    recovered = SENTINEL.discover(
        [provider],
        timeout=1,
        max_bytes=1024,
        now=datetime(2026, 8, 5, 12, tzinfo=UTC),
        previous=second_absence,
        fetch=lambda _url, **_kwargs: (
            200,
            {"data": [{"id": "kept"}, {"id": "candidate-a"}]},
        ),
    )
    row = recovered["providers"][0]
    assert row["recovered_models"] == ["candidate-a"]
    assert row["absence_streaks"] == {}
    assert recovered["drift"]["has_changes"] is True


def test_probe_lifecycle_increments_and_unexpected_errors_are_sanitized(monkeypatch):
    provider = _provider()
    previous = {
        "schema_version": 1,
        "mode": "authenticated_probe",
        "probes": [
            {
                "provider": "example",
                "model": "kept",
                "first_verified": "2026-07-22T12:00:00Z",
                "verification_count": 2,
            }
        ],
    }

    def fail(*_args, **_kwargs):
        raise RuntimeError("secret-provider-response account-123")

    monkeypatch.setattr(SENTINEL.flp_client, "call", fail)
    report = SENTINEL.probe(
        [provider],
        {"EXAMPLE_KEY": "super-secret-provider-key"},
        timeout=1,
        max_providers=1,
        max_models_per_provider=1,
        now=datetime(2026, 7, 29, 12, tzinfo=UTC),
        previous=previous,
    )

    record = report["probes"][0]
    assert record["first_verified"] == "2026-07-22T12:00:00Z"
    assert record["verification_count"] == 3
    assert record["failure_classification"] == "unexpected_probe_error"
    serialized = json.dumps(report)
    assert "super-secret-provider-key" not in serialized
    assert "secret-provider-response" not in serialized
    assert "account-123" not in serialized


def test_probe_repeated_failure_and_recovery_are_advisory_drift(monkeypatch):
    provider = Provider(
        id="example",
        label="Example",
        adapter="openai",
        base_url="https://example.test/v1",
        key_env="EXAMPLE_KEY",
        models=(Model("kept"),),
    )

    def rate_limited(*_args, **_kwargs):
        raise ProviderHTTPError(429, "quota response must not leak", retryable=True)

    monkeypatch.setattr(SENTINEL.flp_client, "call", rate_limited)
    first = SENTINEL.probe(
        [provider],
        {"EXAMPLE_KEY": "secret"},
        timeout=1,
        max_providers=1,
        max_models_per_provider=1,
        now=datetime(2026, 7, 22, 12, tzinfo=UTC),
    )
    row = first["probes"][0]
    assert row["consecutive_failures"] == 1
    assert row["repeated_failure"] is False
    assert row["retirement_candidate"] is False
    assert first["drift"]["has_changes"] is False

    second = SENTINEL.probe(
        [provider],
        {"EXAMPLE_KEY": "secret"},
        timeout=1,
        max_providers=1,
        max_models_per_provider=1,
        now=datetime(2026, 7, 29, 12, tzinfo=UTC),
        previous=first,
    )
    row = second["probes"][0]
    assert row["consecutive_failures"] == 2
    assert row["repeated_failure"] is True
    assert row["failure_threshold_crossed"] is True
    assert row["failure_classification"] == "rate_limited"
    assert row["retirement_candidate"] is False
    assert second["drift"]["has_changes"] is True

    monkeypatch.setattr(
        SENTINEL.flp_client,
        "call",
        lambda *_args, **_kwargs: Reply("pong", "example", "kept", {}),
    )
    recovered = SENTINEL.probe(
        [provider],
        {"EXAMPLE_KEY": "secret"},
        timeout=1,
        max_providers=1,
        max_models_per_provider=1,
        now=datetime(2026, 8, 5, 12, tzinfo=UTC),
        previous=second,
    )
    row = recovered["probes"][0]
    assert row["consecutive_failures"] == 0
    assert row["recovered"] is True
    assert recovered["drift"]["has_changes"] is True


def test_probe_empty_http_success_has_non_success_classification(monkeypatch):
    provider = _provider()
    monkeypatch.setattr(
        SENTINEL.flp_client,
        "call",
        lambda *_args, **_kwargs: Reply(" \n\t", "example", "kept", {}),
    )

    report = SENTINEL.probe(
        [provider],
        {"EXAMPLE_KEY": "secret"},
        timeout=1,
        max_providers=1,
        max_models_per_provider=1,
        now=datetime(2026, 7, 29, 12, tzinfo=UTC),
    )

    row = report["probes"][0]
    assert row["ok"] is False
    assert row["status"] == 200
    assert row["failure_classification"] == "empty_completion"


def test_probe_rotates_across_every_enabled_auto_route_with_bounded_calls(monkeypatch):
    def provider(provider_id: str, *models: Model) -> Provider:
        return Provider(
            id=provider_id,
            label=provider_id,
            adapter="openai",
            base_url=f"https://{provider_id}.test/v1",
            key_env=f"{provider_id.upper()}_KEY",
            models=models,
        )

    providers = [
        provider(
            "alpha",
            Model("auto-a"),
            Model("auto-b"),
            Model("manual", auto=False),
            Model("disabled", enabled=False),
        ),
        provider("beta", Model("auto-a"), Model("auto-b")),
        provider("gamma", Model("auto-a")),
    ]
    secrets = {f"{name.upper()}_KEY": f"{name}-secret" for name in ("alpha", "beta", "gamma")}
    calls: list[tuple[str, str]] = []

    def succeed(current_provider, model, *_args, **_kwargs):
        calls.append((current_provider.id, model))
        return Reply("pong", current_provider.id, model, {})

    monkeypatch.setattr(SENTINEL.flp_client, "call", succeed)
    eligible = {
        ("alpha", "auto-a"),
        ("alpha", "auto-b"),
        ("beta", "auto-a"),
        ("beta", "auto-b"),
        ("gamma", "auto-a"),
    }
    observed: set[tuple[str, str]] = set()
    previous = None
    start = datetime(2026, 8, 26, 12, tzinfo=UTC)

    for week in range(3):
        before = len(calls)
        report = SENTINEL.probe(
            providers,
            secrets,
            timeout=1,
            max_providers=2,
            max_models_per_provider=1,
            now=start + timedelta(days=7 * week),
            previous=previous,
        )
        selected = {(row["provider"], row["model"]) for row in report["probes"]}
        run_calls = calls[before:]

        assert selected == set(run_calls)
        assert len(selected) <= 2
        assert len({provider_id for provider_id, _model in selected}) <= 2
        assert selected <= eligible
        assert report["selection"]["eligible_routes"] == len(eligible)
        assert report["selection"]["batch_count"] == 3
        observed.update(selected)
        previous = report

    assert observed == eligible
    assert all(model not in {"manual", "disabled"} for _provider, model in observed)


def test_probe_uses_utc_week_batches_when_rotation_state_is_unavailable(monkeypatch):
    providers = [
        Provider(
            id=f"provider-{index}",
            label=f"Provider {index}",
            adapter="openai",
            base_url=f"https://provider-{index}.test/v1",
            key_env=f"PROVIDER_{index}_KEY",
            models=(Model("auto"),),
        )
        for index in range(7)
    ]
    secrets = {f"PROVIDER_{index}_KEY": "secret" for index in range(7)}
    monkeypatch.setattr(
        SENTINEL.flp_client,
        "call",
        lambda provider, model, *_args, **_kwargs: Reply(
            "pong", provider.id, model, {}
        ),
    )
    observed: set[tuple[str, str]] = set()
    start = datetime(2026, 8, 26, 12, tzinfo=UTC)

    for week in range(4):
        report = SENTINEL.probe(
            providers,
            secrets,
            timeout=1,
            max_providers=2,
            max_models_per_provider=1,
            now=start + timedelta(days=7 * week),
        )
        selected = {(row["provider"], row["model"]) for row in report["probes"]}
        assert len(selected) <= 2
        assert report["selection"]["batch_count"] == 4
        observed.update(selected)

    assert observed == {(f"provider-{index}", "auto") for index in range(7)}


def test_probe_rotation_preserves_failure_state_for_routes_skipped_between_runs(monkeypatch):
    provider = Provider(
        id="example",
        label="Example",
        adapter="openai",
        base_url="https://example.test/v1",
        key_env="EXAMPLE_KEY",
        models=(Model("auto-a"), Model("auto-b")),
    )

    def rate_limited(*_args, **_kwargs):
        raise ProviderHTTPError(429, "secret provider response", retryable=True)

    monkeypatch.setattr(SENTINEL.flp_client, "call", rate_limited)
    previous = None
    selected: list[str] = []
    reports = []
    start = datetime(2026, 8, 26, 12, tzinfo=UTC)
    for week in range(3):
        report = SENTINEL.probe(
            [provider],
            {"EXAMPLE_KEY": "secret"},
            timeout=1,
            max_providers=1,
            max_models_per_provider=1,
            now=start + timedelta(days=7 * week),
            previous=previous,
        )
        selected.append(report["probes"][0]["model"])
        reports.append(report)
        previous = report

    assert selected[0] != selected[1]
    assert selected[2] == selected[0]
    assert reports[2]["probes"][0]["consecutive_failures"] == 2
    assert reports[2]["probes"][0]["failure_threshold_crossed"] is True
    assert len(reports[2]["probe_state"]) == 2
    assert "secret provider response" not in json.dumps(reports)


def test_listing_failure_after_threshold_does_not_repeat_drift_event():
    provider = _provider()
    previous = {
        "schema_version": 1,
        "mode": "public_discovery",
        "providers": [
            {
                "provider": "example",
                "observed_models": ["kept"],
                "absence_streaks": {"candidate-a": 2},
            }
        ],
    }

    report = SENTINEL.discover(
        [provider],
        timeout=1,
        max_bytes=1024,
        now=datetime(2026, 8, 5, 12, tzinfo=UTC),
        previous=previous,
        fetch=lambda _url, **_kwargs: (429, None),
    )

    row = report["providers"][0]
    assert row["absence_streaks"] == {"candidate-a": 2}
    assert row["absence_threshold_crossed"] == []
    assert report["drift"]["has_changes"] is False


def test_bounded_get_uses_short_transport_timeouts(monkeypatch):
    seen: dict[str, object] = {}

    class Response:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_bytes(self):
            for chunk in (b'{"data":', b"[]", b"}"):
                yield chunk

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(SENTINEL.httpx, "AsyncClient", Client)

    status, payload = SENTINEL._bounded_json_get(
        "https://example.test/models",
        timeout=1.0,
        max_bytes=1024,
    )

    assert status == 200
    assert payload == {"data": []}
    assert seen["timeout"].read <= 1.0


def test_bounded_get_total_deadline_covers_slow_response_headers():
    class SlowHeaderHandler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            self.request.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            for _ in range(20):
                time.sleep(0.05)
                try:
                    self.request.sendall(b"a")
                except OSError:
                    break

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = Server(("127.0.0.1", 0), SlowHeaderHandler)
    runner = threading.Thread(target=server.serve_forever, daemon=True)
    runner.start()
    started = time.monotonic()
    try:
        status, payload = SENTINEL._bounded_json_get(
            f"http://127.0.0.1:{server.server_address[1]}/models",
            timeout=0.2,
            max_bytes=1024,
        )
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()

    assert status is None
    assert payload is None
    assert elapsed < 0.8


def test_issue_body_is_only_generated_for_actionable_drift(tmp_path):
    report = {
        "mode": "public_discovery",
        "generated_at": "2026-07-29T12:00:00Z",
        "advisory_only": True,
        "providers": [
            {
                "provider": "example",
                "failure_classification": "ok",
                "new_models": ["new-model"],
                "removed_models": [],
                "unconfirmed_absences": ["missing"],
            }
        ],
        "drift": {"has_changes": True},
    }
    output = tmp_path / "issue.md"

    assert SENTINEL.write_issue_body(report, output) is True
    body = output.read_text(encoding="utf-8")
    assert "Advisory only" in body
    assert "`example/new-model`" in body
    assert "unconfirmed absence" in body
    assert "providers.toml" in body
    assert "<!-- freellmpool-catalog-sentinel:public:v1 -->" in body

    report["drift"]["has_changes"] = False
    assert SENTINEL.write_issue_body(report, output) is False
    assert not output.exists()


def test_probe_issue_body_contains_classification_but_no_response_content(tmp_path):
    report = {
        "mode": "authenticated_probe",
        "generated_at": "2026-07-29T12:00:00Z",
        "advisory_only": True,
        "probes": [
            {
                "provider": "example",
                "model": "kept",
                "failure_classification": "billing_or_credit",
                "repeated_failure": True,
                "failure_threshold_crossed": True,
                "recovered": False,
                "retirement_candidate": False,
            }
        ],
        "drift": {"has_changes": True},
    }
    output = tmp_path / "probe-issue.md"

    assert SENTINEL.write_issue_body(report, output) is True
    body = output.read_text(encoding="utf-8")
    assert "`example/kept`" in body
    assert "billing_or_credit" in body
    assert "not retirement evidence" in body
    assert "response" not in body.lower()
    assert "<!-- freellmpool-catalog-sentinel:probe:v1 -->" in body


def test_probe_report_contains_no_secret_or_response_content():
    provider = _provider()
    secret = "super-secret-provider-key"
    response = "account-123 private completion text"

    report = SENTINEL.probe_record(
        provider,
        "kept",
        ok=False,
        status=402,
        observed_at=datetime(2026, 7, 29, 12, tzinfo=UTC),
    )
    serialized = json.dumps(report)

    assert secret not in serialized
    assert response not in serialized
    assert report["failure_classification"] == "billing_or_credit"
    assert report["last_verified"] == "2026-07-29T12:00:00Z"
    assert report["verification_count"] == 1
    assert set(report) == {
        "provider",
        "model",
        "ok",
        "status",
        "failure_classification",
        "first_verified",
        "last_verified",
        "last_successful_verification",
        "verification_count",
        "consecutive_failures",
        "repeated_failure",
        "failure_threshold_crossed",
        "recovered",
        "retirement_candidate",
        "advisory_only",
    }


def test_secret_map_is_strict_and_bounded(monkeypatch):
    monkeypatch.setenv(
        "FREELLMPOOL_SENTINEL_KEYS_JSON",
        json.dumps({"EXAMPLE_KEY": "secret", "CLOUDFLARE_ACCOUNT_ID": "account"}),
    )
    assert SENTINEL.load_secret_map() == {
        "EXAMPLE_KEY": "secret",
        "CLOUDFLARE_ACCOUNT_ID": "account",
    }

    monkeypatch.setenv("FREELLMPOOL_SENTINEL_KEYS_JSON", '["not", "an", "object"]')
    with pytest.raises(ValueError, match="object"):
        SENTINEL.load_secret_map()

    monkeypatch.setenv("FREELLMPOOL_SENTINEL_KEYS_JSON", "{}")
    with pytest.raises(ValueError, match="non-empty"):
        SENTINEL.load_secret_map()


def test_workflow_is_advisory_least_privilege_and_fork_safe():
    workflow = (ROOT / ".github" / "workflows" / "catalog-sentinel.yml").read_text(
        encoding="utf-8"
    )

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "environment: catalog-sentinel" in workflow
    assert "issues: write" in workflow
    assert "actions: write" not in workflow
    assert "contents: write" not in workflow
    assert "concurrency:" in workflow
    assert "timeout-minutes:" in workflow
    assert "actions/cache/restore@" in workflow
    assert "actions/cache/save@" in workflow
    assert "actions/upload-artifact@" in workflow
    assert "uses: actions/checkout@" in workflow
    assert "uses: actions/setup-python@" in workflow
    assert "gh issue create" in workflow
    assert "gh issue comment" in workflow
    assert "Catalog sentinel probe findings" in workflow
    assert "github-actions[bot]" in workflow
    assert "<!-- freellmpool-catalog-sentinel:public:v1 -->" in workflow
    assert "<!-- freellmpool-catalog-sentinel:probe:v1 -->" in workflow
    assert ".author.login" in workflow
    assert ".body | contains(" in workflow
    assert ".title ==" in workflow
    assert "--body-file" in workflow
    assert "scripts/catalog_sentinel.py discover" in workflow
    assert "scripts/catalog_sentinel.py probe" in workflow
    assert "freellmpool conformance run" in workflow
    assert "FREELLMPOOL_CONFORMANCE_KEYS_JSON" in workflow
    assert ".sentinel-state/conformance.json" in workflow
    assert ".sentinel-artifacts/conformance.json" in workflow
    assert "--previous" in workflow
    assert "FREELLMPOOL_SENTINEL_KEYS_JSON" in workflow
    assert "Never mutates providers.toml" in workflow


def test_authenticated_workflow_fails_visibly_when_protected_credentials_are_missing():
    workflow = (ROOT / ".github" / "workflows" / "catalog-sentinel.yml").read_text(
        encoding="utf-8"
    )
    protected = workflow.split("  authenticated-probes:", 1)[1]

    assert "FREELLMPOOL_SENTINEL_KEYS_JSON" in protected
    assert "::error" in protected
    assert "exit 1" in protected
    assert "configured=false" not in protected
    assert "steps.probe-config.outputs.configured" not in protected


def test_authenticated_workflow_timeout_covers_declared_probe_budget():
    workflow = (ROOT / ".github" / "workflows" / "catalog-sentinel.yml").read_text(
        encoding="utf-8"
    )
    protected = workflow.split("  authenticated-probes:", 1)[1]
    timeout_minutes = int(re.search(r"timeout-minutes:\s*(\d+)", protected).group(1))

    probe = protected.split("python scripts/catalog_sentinel.py probe", 1)[1].split(
        "cp .sentinel-artifacts/probe.json", 1
    )[0]
    probe_timeout = int(re.search(r"--timeout\s+(\d+)", probe).group(1))
    max_providers = int(re.search(r"--max-providers\s+(\d+)", probe).group(1))
    max_models = int(
        re.search(r"--max-models-per-provider\s+(\d+)", probe).group(1)
    )

    canaries = protected.split("freellmpool conformance run", 1)[1].split(
        "cp .sentinel-state/conformance.json", 1
    )[0]
    conformance_timeout = int(re.search(r"--timeout\s+(\d+)", canaries).group(1))
    max_targets = int(re.search(r"--max-targets\s+(\d+)", canaries).group(1))
    features = re.search(r"--features\s+([a-z_,]+)", canaries).group(1).split(",")

    network_budget_seconds = (
        probe_timeout * max_providers * max_models
        + conformance_timeout * max_targets * len(features)
    )
    assert timeout_minutes * 60 >= network_budget_seconds + 600


def test_catalog_sentinel_operator_contract_is_documented():
    doc = (ROOT / "docs" / "CATALOG_SENTINEL.md").read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

    for phrase in (
        "catalog-sentinel",
        "FREELLMPOOL_SENTINEL_KEYS_JSON",
        "environment protection",
        "advisory",
        "never enables or disables",
        "429",
        "402",
        "workflow artifact",
    ):
        assert phrase in doc
    assert "docs/CATALOG_SENTINEL.md" in contributing
