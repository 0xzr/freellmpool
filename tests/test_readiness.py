from __future__ import annotations

from freellmpool.models import Model, Provider
from freellmpool.readiness import readiness_snapshot


def _provider(
    *,
    provider_id: str = "alpha",
    key_env: str | None = "ALPHA_KEY",
    auth: str = "bearer",
    models: tuple[Model, ...] = (Model("small", rpd=2),),
) -> Provider:
    return Provider(
        id=provider_id,
        label=provider_id.title(),
        adapter="openai",
        base_url=f"https://{provider_id}.test/v1",
        key_env=key_env,
        auth=auth,
        models=models,
    )


def test_readiness_snapshot_ready_schema_and_unknown_quota() -> None:
    limited = _provider()
    unmetered = _provider(
        provider_id="free",
        key_env=None,
        auth="none",
        models=(Model("unknown", rpd=0),),
    )

    snapshot = readiness_snapshot(
        [limited, unmetered],
        env={"ALPHA_KEY": "secret"},
        quota={"alpha::small": 1},
        cooldowns={},
    )

    assert snapshot.ready_providers == 2
    assert snapshot.total_providers == 2
    assert snapshot.summary == {
        "ready": 2,
        "unconfigured": 0,
        "no_enabled_models": 0,
        "cooldown": 0,
        "quota_exhausted": 0,
    }
    payload = snapshot.provider_payload()
    assert payload["schema_version"] == 1
    assert payload["object"] == "list"
    assert payload["data"] == [
        {
            "id": "alpha",
            "configured": True,
            "ready": True,
            "status": "ready",
            "enabled_models": 1,
            "ready_models": 1,
            "cooldown_remaining_s": 0.0,
            "models": [
                {
                    "id": "alpha/small",
                    "name": "small",
                    "ready": True,
                    "status": "ready",
                    "daily_limit": 2,
                    "used_today": 1,
                    "remaining": 1,
                }
            ],
        },
        {
            "id": "free",
            "configured": True,
            "ready": True,
            "status": "ready",
            "enabled_models": 1,
            "ready_models": 1,
            "cooldown_remaining_s": 0.0,
            "models": [
                {
                    "id": "free/unknown",
                    "name": "unknown",
                    "ready": True,
                    "status": "ready",
                    "daily_limit": None,
                    "used_today": 0,
                    "remaining": None,
                }
            ],
        },
    ]
    assert snapshot.ready_model_ids == ("alpha/small", "free/unknown")


def test_readiness_status_precedence_and_disabled_models() -> None:
    unconfigured_empty = _provider(models=())
    no_models = _provider(
        provider_id="empty",
        key_env=None,
        auth="none",
        models=(Model("off", enabled=False),),
    )
    cooled_exhausted = _provider(provider_id="cooled", key_env="COOLED_KEY")
    exhausted = _provider(provider_id="spent", key_env="SPENT_KEY")

    snapshot = readiness_snapshot(
        [unconfigured_empty, no_models, cooled_exhausted, exhausted],
        env={"COOLED_KEY": "c", "SPENT_KEY": "s"},
        quota={"cooled::small": 2, "spent::small": 2},
        cooldowns={"cooled": 12.345},
    )

    assert [provider.status for provider in snapshot.providers] == [
        "unconfigured",
        "no_enabled_models",
        "cooldown",
        "quota_exhausted",
    ]
    assert snapshot.ready_providers == 0
    assert snapshot.ready_model_ids == ()
    assert snapshot.providers[2].cooldown_remaining_s == 12.345
    assert snapshot.readiness_payload() == {
        "schema_version": 1,
        "status": "not_ready",
        "reason": "no_ready_providers",
        "ready_providers": 0,
        "total_providers": 4,
        "summary": {
            "ready": 0,
            "unconfigured": 1,
            "no_enabled_models": 1,
            "cooldown": 1,
            "quota_exhausted": 1,
        },
    }


def test_readiness_model_statuses_follow_provider_precedence() -> None:
    provider = _provider(models=(Model("spent", rpd=1), Model("open", rpd=3)))

    exhausted = readiness_snapshot(
        [provider],
        env={"ALPHA_KEY": "a"},
        quota={"alpha::spent": 1, "alpha::open": 3},
        cooldowns={},
    ).providers[0]
    assert [model.status for model in exhausted.models] == [
        "quota_exhausted",
        "quota_exhausted",
    ]

    cooled = readiness_snapshot(
        [provider],
        env={"ALPHA_KEY": "a"},
        quota={"alpha::spent": 1},
        cooldowns={"alpha": 1.0},
    ).providers[0]
    assert [model.status for model in cooled.models] == ["cooldown", "cooldown"]

    mixed = readiness_snapshot(
        [provider],
        env={"ALPHA_KEY": "a"},
        quota={"alpha::spent": 1},
        cooldowns={},
    ).providers[0]
    assert mixed.status == "ready"
    assert [model.status for model in mixed.models] == ["quota_exhausted", "ready"]
    assert mixed.ready_models == 1
