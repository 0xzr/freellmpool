from __future__ import annotations

from freellmpool.capacity import build_capacity_report
from freellmpool.key_inventory import KeyRecord
from freellmpool.models import Model, Provider


class FakeQuota:
    def __init__(self, snap):
        self._snap = snap

    def snapshot(self):
        return dict(self._snap)


def _catalog():
    return [
        Provider(
            id="keyless",
            label="Keyless",
            adapter="openai",
            base_url="https://example.test/v1",
            auth="none",
            models=(Model("a", rpd=10),),
        ),
        Provider(
            id="needskey",
            label="Needs Key",
            adapter="openai",
            base_url="https://example.test/v1",
            key_env="NEEDS_KEY",
            models=(Model("b", rpd=10),),
        ),
        Provider(
            id="low",
            label="Low",
            adapter="openai",
            base_url="https://example.test/v1",
            key_env="LOW_KEY",
            models=(Model("c", rpd=10),),
        ),
    ]


def test_capacity_marks_configured_missing_and_low_quota():
    report = build_capacity_report(
        target=3,
        env={"LOW_KEY": "value"},
        quota=FakeQuota({"low::c": 8}),
        catalog=_catalog(),
    )
    statuses = {p.provider_id: p.status for p in report.providers}
    assert statuses["keyless"] == "healthy"
    assert statuses["needskey"] == "missing"
    assert statuses["low"] == "low_quota"
    assert report.healthy_count == 1
    assert report.low_quota_count == 1


def test_capacity_checklist_returns_missing_key_provider():
    report = build_capacity_report(
        target=3,
        env={"NEEDS_KEY": "value"},
        quota=FakeQuota({}),
        catalog=_catalog(),
    )
    checklist = report.checklist()
    assert [p.provider_id for p in checklist] == ["low"]
    assert checklist[0].key_env == "LOW_KEY"


def test_inventory_expiry_marks_invalid_key():
    report = build_capacity_report(
        target=1,
        env={"NEEDS_KEY": "value"},
        quota=FakeQuota({}),
        catalog=[_catalog()[1]],
        inventory=[KeyRecord(provider="needskey", env_var="NEEDS_KEY", expires_at="2000-01-01")],
    )
    assert report.providers[0].status == "invalid_key"


def test_malformed_inventory_expiry_does_not_mask_expired_date():
    report = build_capacity_report(
        target=1,
        env={"NEEDS_KEY": "value"},
        quota=FakeQuota({}),
        catalog=[_catalog()[1]],
        inventory=[
            KeyRecord(provider="needskey", env_var="OLD_KEY", expires_at="2000-01-01"),
            KeyRecord(provider="needskey", env_var="NEEDS_KEY", expires_at="!garbage"),
        ],
    )
    assert report.providers[0].expires_at == "2000-01-01"
    assert report.providers[0].status == "invalid_key"


def test_inventory_expiry_compares_parsed_iso_dates():
    report = build_capacity_report(
        target=1,
        env={"NEEDS_KEY": "value"},
        quota=FakeQuota({}),
        catalog=[_catalog()[1]],
        inventory=[
            KeyRecord(provider="needskey", env_var="OLD_KEY", expires_at="20000101"),
            KeyRecord(provider="needskey", env_var="NEEDS_KEY", expires_at="2030-12-31"),
        ],
    )
    assert report.providers[0].expires_at == "2000-01-01"
    assert report.providers[0].status == "invalid_key"


def test_inventory_with_only_malformed_expiry_has_no_expiry():
    report = build_capacity_report(
        target=1,
        env={"NEEDS_KEY": "value"},
        quota=FakeQuota({}),
        catalog=[_catalog()[1]],
        inventory=[
            KeyRecord(provider="needskey", env_var="NEEDS_KEY", expires_at="!garbage")
        ],
    )
    assert report.providers[0].expires_at is None
    assert report.providers[0].status == "healthy"


def test_capacity_sorts_by_generosity_within_status():
    catalog = [
        Provider(
            id="small",
            label="Small",
            adapter="openai",
            base_url="https://example.test/v1",
            auth="none",
            models=(Model("a", rpd=10),),
        ),
        Provider(
            id="large",
            label="Large",
            adapter="openai",
            base_url="https://example.test/v1",
            auth="none",
            models=(Model("b", rpd=1000),),
        ),
    ]
    report = build_capacity_report(target=2, env={}, quota=FakeQuota({}), catalog=catalog)
    assert [p.provider_id for p in report.providers] == ["large", "small"]
