#!/usr/bin/env python3
"""Advisory-only catalog drift discovery and bounded completion canaries."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from freellmpool import client as flp_client  # noqa: E402
from freellmpool.catalog_validation import normalize_model_listing  # noqa: E402
from freellmpool.config import load_catalog  # noqa: E402
from freellmpool.errors import ProviderHTTPError  # noqa: E402
from freellmpool.models import Provider  # noqa: E402

_MAX_BODY_BYTES = 1_000_000
_MAX_SECRET_JSON_BYTES = 32_000
_MAX_PREVIOUS_BYTES = 2_000_000
_MAX_ISSUE_BODY_BYTES = 60_000
_PING = [{"role": "user", "content": "Reply with the single word: pong"}]
# Only endpoints verified to expose a complete public chat-model listing belong
# here. Everything else is useful for additions, but absences remain unconfirmed.
_AUTHORITATIVE_PUBLIC_LISTINGS = frozenset({"pollinations"})


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def classify_http(status: int | None) -> str:
    if status == 200:
        return "ok"
    if status in {401, 403}:
        return "auth_required"
    if status == 402:
        return "billing_or_credit"
    if status == 404:
        return "listing_unsupported"
    if status == 429:
        return "rate_limited"
    if status is None:
        return "network_or_timeout"
    if 500 <= status < 600:
        return "transient_provider_error"
    return "other_provider_error"


def _public_models_url(provider: Provider) -> str:
    if provider.id == "pollinations":
        return "https://text.pollinations.ai/models"
    return f"{provider.base_url.rstrip('/')}/models"


async def _bounded_json_get_async(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
) -> tuple[int, Any]:
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(
            timeout,
            connect=min(timeout, 3.0),
            read=min(timeout, 2.0),
            write=min(timeout, 2.0),
            pool=min(timeout, 2.0),
        ),
        headers={"User-Agent": "freellmpool-catalog-sentinel/1"},
    ) as client:
        async with client.stream("GET", url) as response:
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    return response.status_code, None
                chunks.append(chunk)
            raw = b"".join(chunks)
            if not raw:
                return response.status_code, None
            try:
                return response.status_code, json.loads(raw)
            except (UnicodeError, json.JSONDecodeError):
                return response.status_code, None


def _bounded_json_get(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
) -> tuple[int | None, Any]:
    try:
        return asyncio.run(
            asyncio.wait_for(
                _bounded_json_get_async(
                    url,
                    timeout=timeout,
                    max_bytes=max_bytes,
                ),
                timeout=timeout,
            )
        )
    except (TimeoutError, httpx.HTTPError):
        return None, None


def discovery_record(
    provider: Provider,
    *,
    status: int | None,
    payload: Any,
    observed_at: datetime,
    listing_authoritative: bool = False,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    live = normalize_model_listing(payload) if status == 200 else ()
    classification = (
        "invalid_or_empty_listing"
        if status == 200 and not live
        else classify_http(status)
    )
    catalog = {model.name for model in provider.models}
    listing_complete = status == 200 and bool(live) and listing_authoritative
    absences = sorted(catalog - set(live)) if status == 200 and live else []
    timestamp = _timestamp(observed_at)
    first_discovered = _safe_timestamp((previous or {}).get("first_discovered")) or timestamp
    discovery_count = _bounded_count((previous or {}).get("discovery_count")) + 1
    return {
        "provider": provider.id,
        "label": provider.label,
        "failure_classification": classification,
        "status": status,
        "listing_complete": listing_complete,
        "listing_scope": "authoritative" if listing_complete else "partial_or_unknown",
        "live_model_count": len(live),
        "observed_models": list(live),
        "catalog_gaps": sorted(set(live) - catalog) if live else [],
        "catalog_unlisted_models": absences,
        "new_models": sorted(set(live) - catalog) if live else [],
        "removed_models": absences if listing_complete else [],
        "unconfirmed_absences": [] if listing_complete else absences,
        "recovered_models": [],
        "absence_streaks": {},
        "repeated_absences": [],
        "absence_threshold_crossed": [],
        "baseline_initialized": bool(live),
        # Discovery is evidence for maintainer review, never a routing mutation or
        # retirement recommendation. Completion canaries provide separate evidence.
        "retirement_candidates": [],
        "first_discovered": first_discovered,
        "last_discovered": timestamp,
        "discovery_count": discovery_count,
        "last_verified": None,
        "verification_count": 0,
        "free_tier_kind": "unknown",
        "billing_risk": "review_required",
        "region_privacy_notes": "not_recorded",
        "advisory_only": True,
    }


def probe_record(
    provider: Provider,
    model: str,
    *,
    ok: bool,
    status: int | None,
    observed_at: datetime,
    previous: dict[str, Any] | None = None,
    failure_classification: str | None = None,
) -> dict[str, Any]:
    timestamp = _timestamp(observed_at)
    first_verified = _safe_timestamp((previous or {}).get("first_verified")) or timestamp
    prior_failures = _bounded_count((previous or {}).get("consecutive_failures"))
    consecutive_failures = 0 if ok else min(1_000_000_000, prior_failures + 1)
    return {
        "provider": provider.id,
        "model": model,
        "ok": ok,
        "status": status,
        "failure_classification": (
            "ok" if ok else failure_classification or classify_http(status)
        ),
        "first_verified": first_verified,
        "last_verified": timestamp,
        "last_successful_verification": (
            timestamp
            if ok
            else _safe_timestamp((previous or {}).get("last_successful_verification"))
        ),
        "verification_count": _bounded_count((previous or {}).get("verification_count")) + 1,
        "consecutive_failures": consecutive_failures,
        "repeated_failure": not ok and consecutive_failures >= 2,
        "failure_threshold_crossed": not ok and consecutive_failures == 2,
        "recovered": ok and prior_failures > 0,
        "retirement_candidate": False,
        "advisory_only": True,
    }


def _safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 32:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return _timestamp(parsed)


def _bounded_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, min(value, 1_000_000_000))


def _safe_model_set(value: Any) -> set[str] | None:
    if not isinstance(value, list):
        return None
    return set(normalize_model_listing(value))


def _safe_absence_streaks(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for model, count in value.items():
        normalized = normalize_model_listing([model])
        if len(normalized) == 1:
            result[normalized[0]] = _bounded_count(count)
    return result


def _apply_discovery_lifecycle(
    record: dict[str, Any],
    previous: dict[str, Any] | None,
) -> None:
    prior_models = _safe_model_set((previous or {}).get("observed_models"))
    prior_streaks = _safe_absence_streaks((previous or {}).get("absence_streaks"))
    current_models = set(record["observed_models"])
    listing_worked = record["status"] == 200 and bool(current_models)

    if not listing_worked:
        record["observed_models"] = sorted(prior_models or ())
        record["absence_streaks"] = prior_streaks
        record["baseline_initialized"] = prior_models is not None
        record["new_models"] = []
        record["removed_models"] = []
        record["unconfirmed_absences"] = []
        record["recovered_models"] = []
        record["repeated_absences"] = sorted(
            model for model, count in prior_streaks.items() if count >= 2
        )
        record["absence_threshold_crossed"] = []
        return

    record["baseline_initialized"] = True
    if prior_models is None:
        # The first successful observation establishes state. Catalog gaps stay
        # visible in the artifact but are not mislabeled as newly free routes.
        if not record["listing_complete"]:
            record["new_models"] = []
            record["removed_models"] = []
        record["unconfirmed_absences"] = []
        record["recovered_models"] = []
        record["absence_streaks"] = {}
        record["repeated_absences"] = []
        record["absence_threshold_crossed"] = []
        return

    tracked_prior = prior_models | set(prior_streaks)
    absent = sorted(tracked_prior - current_models)
    recovered = sorted(current_models & set(prior_streaks))
    added = sorted((current_models - prior_models) - set(recovered))
    streaks = {
        model: min(1_000_000_000, prior_streaks.get(model, 0) + 1)
        for model in absent
    }
    record["new_models"] = added
    record["removed_models"] = absent if record["listing_complete"] else []
    record["unconfirmed_absences"] = [] if record["listing_complete"] else absent
    record["recovered_models"] = recovered
    record["absence_streaks"] = streaks
    record["repeated_absences"] = sorted(
        model for model, count in streaks.items() if count >= 2
    )
    record["absence_threshold_crossed"] = sorted(
        model
        for model, count in streaks.items()
        if count == 2 and prior_streaks.get(model, 0) < 2
    )


def _previous_rows(
    previous: dict[str, Any] | None,
    *,
    mode: str,
    collection: str,
    keys: tuple[str, ...],
) -> dict[tuple[str, ...], dict[str, Any]]:
    if (
        not isinstance(previous, dict)
        or previous.get("schema_version") != 1
        or previous.get("mode") != mode
    ):
        return {}
    rows = previous.get(collection)
    if not isinstance(rows, list):
        return {}
    result: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity: list[str] = []
        for key in keys:
            value = row.get(key)
            if not isinstance(value, str):
                break
            identity.append(value)
        else:
            result[tuple(identity)] = row
    return result


def load_previous(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > _MAX_PREVIOUS_BYTES:
        return None
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_secret_map(
    env: dict[str, str] | None = None,
    *,
    variable: str = "FREELLMPOOL_SENTINEL_KEYS_JSON",
) -> dict[str, str]:
    source = env if env is not None else os.environ
    raw = source.get(variable, "")
    if not raw:
        raise ValueError(f"{variable} is required for authenticated probes")
    if len(raw.encode("utf-8")) > _MAX_SECRET_JSON_BYTES:
        raise ValueError("secret map exceeds size limit")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("secret map must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("secret map must be an object")
    result: dict[str, str] = {}
    for key, value in payload.items():
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or not value
            or len(key) > 128
            or len(value) > 8_192
        ):
            raise ValueError("secret map must contain bounded non-empty strings")
        result[key] = value
    return result


def discover(
    providers: list[Provider],
    *,
    timeout: float,
    max_bytes: int,
    now: datetime,
    previous: dict[str, Any] | None = None,
    fetch: Callable[..., tuple[int | None, Any]] = _bounded_json_get,
) -> dict[str, Any]:
    prior = _previous_rows(
        previous,
        mode="public_discovery",
        collection="providers",
        keys=("provider",),
    )
    records: list[dict[str, Any]] = []
    for provider in providers:
        try:
            status, payload = fetch(
                _public_models_url(provider),
                timeout=timeout,
                max_bytes=max_bytes,
            )
        except Exception:  # noqa: BLE001 - isolate one discovery endpoint
            status, payload = None, None
        previous_record = prior.get((provider.id,))
        record = discovery_record(
            provider,
            status=status,
            payload=payload,
            observed_at=now,
            listing_authoritative=provider.id in _AUTHORITATIVE_PUBLIC_LISTINGS,
            previous=previous_record,
        )
        _apply_discovery_lifecycle(record, previous_record)
        records.append(record)
    has_changes = any(
        record["new_models"]
        or record["removed_models"]
        or record["recovered_models"]
        or record["absence_threshold_crossed"]
        for record in records
    )
    return {
        "schema_version": 1,
        "mode": "public_discovery",
        "generated_at": _timestamp(now),
        "advisory_only": True,
        "providers": records,
        "drift": {"has_changes": has_changes},
    }


def probe(
    providers: list[Provider],
    secrets: dict[str, str],
    *,
    timeout: float,
    max_providers: int,
    max_models_per_provider: int,
    now: datetime,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    prior = _previous_rows(
        previous,
        mode="authenticated_probe",
        collection="probes",
        keys=("provider", "model"),
    )
    selected = [
        provider
        for provider in providers
        if provider.is_configured(secrets)
    ][:max_providers]
    for provider in selected:
        models = [model for model in provider.models if model.enabled][
            :max_models_per_provider
        ]
        for model in models:
            try:
                reply = flp_client.call(
                    provider,
                    model.name,
                    _PING,
                    api_key=provider.api_key(secrets),
                    env=secrets,
                    max_tokens=8,
                    temperature=0.0,
                    timeout=timeout,
                    enforce_thinking_floor=False,
                )
                ok = bool(reply.text.strip())
                status: int | None = 200
                failure_classification = None if ok else "empty_completion"
            except ProviderHTTPError as exc:
                ok = False
                status = exc.status
                failure_classification = None
            except (httpx.HTTPError, OSError):
                ok = False
                status = None
                failure_classification = None
            except Exception:  # noqa: BLE001 - never serialize provider exception text
                ok = False
                status = None
                failure_classification = "unexpected_probe_error"
            records.append(
                probe_record(
                    provider,
                    model.name,
                    ok=ok,
                    status=status,
                    observed_at=now,
                    previous=prior.get((provider.id, model.name)),
                    failure_classification=failure_classification,
                )
            )
    report = {
        "schema_version": 1,
        "mode": "authenticated_probe",
        "generated_at": _timestamp(now),
        "advisory_only": True,
        "bounds": {
            "max_providers": max_providers,
            "max_models_per_provider": max_models_per_provider,
        },
        "probes": records,
    }
    report["drift"] = {
        "has_changes": any(
            record["recovered"] or record["failure_threshold_crossed"]
            for record in records
        )
    }
    return report


def render_summary(report: dict[str, Any]) -> str:
    lines = [
        "# Catalog sentinel",
        "",
        "Advisory only. Never mutates providers.toml or routing state.",
        "",
    ]
    if report["mode"] == "public_discovery":
        for record in report["providers"]:
            lines.append(
                f"- `{record['provider']}`: {record['failure_classification']}; "
                f"{len(record['new_models'])} new, {len(record['removed_models'])} removed, "
                f"{len(record.get('recovered_models', []))} recovered, "
                f"{len(record.get('repeated_absences', []))} repeatedly absent"
            )
    else:
        ok = sum(1 for record in report["probes"] if record["ok"])
        lines.append(f"- bounded probes passing: {ok}/{len(report['probes'])}")
    return "\n".join(lines) + "\n"


def _write_outputs(report: dict[str, Any], output: Path, summary: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary.write_text(render_summary(report), encoding="utf-8")


def _inline_code(value: str) -> str:
    return f"`{value.replace('`', '')}`"


def write_issue_body(report: dict[str, Any], output: Path) -> bool:
    drift = report.get("drift") if isinstance(report, dict) else None
    if not isinstance(drift, dict) or not drift.get("has_changes"):
        output.unlink(missing_ok=True)
        return False
    mode = report.get("mode")
    heading = (
        "Catalog sentinel probe findings"
        if mode == "authenticated_probe"
        else "Catalog sentinel drift"
    )
    marker = (
        "<!-- freellmpool-catalog-sentinel:probe:v1 -->"
        if mode == "authenticated_probe"
        else "<!-- freellmpool-catalog-sentinel:public:v1 -->"
    )
    lines = [
        marker,
        "",
        f"## {heading}",
        "",
        f"Generated at {_inline_code(str(report.get('generated_at') or 'unknown'))}.",
        "",
        "Advisory only. This report never mutates `providers.toml` or routing state.",
        "Every catalog change requires maintainer review and live verification.",
        "",
    ]
    if mode == "authenticated_probe":
        for record in report.get("probes", []):
            if not isinstance(record, dict):
                continue
            target = f"{record.get('provider')}/{record.get('model')}"
            if record.get("failure_threshold_crossed"):
                classification = str(
                    record.get("failure_classification") or "unknown"
                )
                lines.append(
                    "- repeatedly failed "
                    f"({_inline_code(classification)}; not retirement evidence): "
                    f"{_inline_code(target)}"
                )
            if record.get("recovered"):
                lines.append(f"- recovered after prior failures: {_inline_code(target)}")
    else:
        for record in report.get("providers", []):
            if not isinstance(record, dict):
                continue
            provider = str(record.get("provider") or "")
            for model in record.get("new_models", []):
                lines.append(f"- new model: {_inline_code(f'{provider}/{model}')}")
            for model in record.get("removed_models", []):
                lines.append(
                    "- removed from authoritative listing: "
                    f"{_inline_code(f'{provider}/{model}')}"
                )
            for model in record.get("unconfirmed_absences", []):
                lines.append(
                    "- unconfirmed absence (partial/unknown listing): "
                    f"{_inline_code(f'{provider}/{model}')}"
                )
            for model in record.get("absence_threshold_crossed", []):
                lines.append(
                    "- repeatedly absent, still not retirement evidence: "
                    f"{_inline_code(f'{provider}/{model}')}"
                )
            for model in record.get("recovered_models", []):
                lines.append(
                    f"- recovered in public listing: {_inline_code(f'{provider}/{model}')}"
                )
    lines.extend(
        [
            "",
            "Do not enable, disable, or retire routes from this report alone.",
            "",
        ]
    )
    body = "\n".join(lines)
    encoded = body.encode("utf-8")
    if len(encoded) > _MAX_ISSUE_BODY_BYTES:
        suffix = "\n\n_Report truncated; inspect the workflow artifact for the bounded full report._\n"
        body = encoded[: _MAX_ISSUE_BODY_BYTES - len(suffix.encode("utf-8"))].decode(
            "utf-8", errors="ignore"
        ) + suffix
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    discovery = subparsers.add_parser("discover")
    discovery.add_argument("--output", type=Path, required=True)
    discovery.add_argument("--summary", type=Path, required=True)
    discovery.add_argument("--timeout", type=float, default=10.0)
    discovery.add_argument("--max-bytes", type=int, default=_MAX_BODY_BYTES)
    discovery.add_argument("--previous", type=Path)

    canary = subparsers.add_parser("probe")
    canary.add_argument("--output", type=Path, required=True)
    canary.add_argument("--summary", type=Path, required=True)
    canary.add_argument("--timeout", type=float, default=20.0)
    canary.add_argument("--max-providers", type=int, default=8)
    canary.add_argument("--max-models-per-provider", type=int, default=1)
    canary.add_argument("--previous", type=Path)

    issue = subparsers.add_parser("issue-body")
    issue.add_argument("--report", type=Path, required=True)
    issue.add_argument("--output", type=Path, required=True)

    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    if args.command == "issue-body":
        report = load_previous(args.report)
        if report is None:
            parser.error("report must be bounded valid JSON")
        return 0 if write_issue_body(report, args.output) else 3

    # Scheduled automation must use only the reviewed, packaged catalog. Loading
    # a user override here would turn a local URL into a workflow SSRF target.
    providers = load_catalog(path=SRC / "freellmpool" / "providers.toml")
    previous = load_previous(args.previous)
    if args.command == "discover":
        if not 0 < args.timeout <= 30 or not 1 <= args.max_bytes <= _MAX_BODY_BYTES:
            parser.error("discovery bounds are out of range")
        report = discover(
            providers,
            timeout=args.timeout,
            max_bytes=args.max_bytes,
            now=now,
            previous=previous,
        )
    else:
        if (
            not 0 < args.timeout <= 60
            or not 1 <= args.max_providers <= 24
            or not 1 <= args.max_models_per_provider <= 2
        ):
            parser.error("probe bounds are out of range")
        try:
            secrets = load_secret_map()
        except ValueError as exc:
            parser.error(str(exc))
        report = probe(
            providers,
            secrets,
            timeout=args.timeout,
            max_providers=args.max_providers,
            max_models_per_provider=args.max_models_per_provider,
            now=now,
            previous=previous,
        )
    _write_outputs(report, args.output, args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
