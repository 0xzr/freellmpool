#!/usr/bin/env python3
"""Bounded, secret-safe acceptance checks for Vercel AI Gateway.

Public pricing is checked for every automatic Vercel route before a credential
is used. The default completion canary is an explicitly zero-priced route.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from freellmpool import client as flp_client  # type: ignore[import-untyped]  # noqa: E402
from freellmpool.config import (  # type: ignore[import-untyped]  # noqa: E402
    effective_env,
    load_catalog,
)
from freellmpool.errors import ProviderHTTPError  # type: ignore[import-untyped]  # noqa: E402
from freellmpool.models import Provider  # type: ignore[import-untyped]  # noqa: E402

BASE_URL = "https://ai-gateway.vercel.sh/v1"
MODELS_URL = f"{BASE_URL}/models"
DEFAULT_MODEL = "poolside/laguna-s-2.1-free"
PACKAGED_CATALOG = SRC / "freellmpool" / "providers.toml"
MAX_BODY_BYTES = 1_000_000
TIMEOUT_SECONDS = 20.0
ATTEMPTS = 3
MAX_TOKENS = 8
_CANARY = [{"role": "user", "content": "Reply with exactly OK."}]
FetchFn = Callable[..., Any]
PostFn = Callable[[str, dict[str, str], dict[str, Any], float], flp_client.HTTPResult]
_DIRECT_PRICE_FIELDS = frozenset(
    {
        "prompt",
        "completion",
        "input",
        "output",
        "request",
        "image",
        "image_output",
        "internal_reasoning",
        "web_search",
        "input_cache_read",
        "input_cache_write",
        "discount",
    }
)


class VerificationError(RuntimeError):
    """A fixed-classification failure that never contains an upstream message."""

    def __init__(self, classification: str) -> None:
        self.classification = classification
        super().__init__(classification)


def _decimal(value: Any, classification: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise VerificationError(classification)
    raw = str(value)
    if len(raw) > 64:
        raise VerificationError(classification)
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise VerificationError(classification) from None
    if (
        not number.is_finite()
        or number < 0
        or (number != 0 and not -30 <= number.adjusted() <= 6)
    ):
        raise VerificationError(classification)
    return number


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _pricing_values(pricing: Any) -> list[Decimal]:
    if not isinstance(pricing, dict):
        raise VerificationError("invalid_model_pricing")
    values: list[Decimal] = []
    for name, raw in pricing.items():
        if name in _DIRECT_PRICE_FIELDS:
            values.append(_decimal(raw, "invalid_model_pricing"))
            continue
        if not name.endswith("_tiers"):
            continue
        if not isinstance(raw, list):
            raise VerificationError("invalid_model_pricing")
        for tier in raw:
            if not isinstance(tier, dict) or "cost" not in tier:
                raise VerificationError("invalid_model_pricing")
            values.append(_decimal(tier["cost"], "invalid_model_pricing"))
    if not values:
        raise VerificationError("invalid_model_pricing")
    return values


def _validate_provider(provider: Provider) -> None:
    if (
        provider.id != "vercel"
        or provider.adapter != "openai"
        or provider.base_url != BASE_URL
        or provider.key_env != "AI_GATEWAY_API_KEY"
        or provider.auth != "bearer"
    ):
        raise VerificationError("unsafe_provider_configuration")


def _read_bounded(response: httpx.Response, max_bytes: int) -> bytes:
    raw = bytearray()
    for chunk in response.iter_bytes():
        raw.extend(chunk)
        if len(raw) > max_bytes:
            raise VerificationError("response_too_large")
    return bytes(raw)


def _bounded_json_get(
    url: str,
    *,
    timeout: float = TIMEOUT_SECONDS,
    max_bytes: int = MAX_BODY_BYTES,
) -> Any:
    if not (url == MODELS_URL or (url.startswith(MODELS_URL + "/") and url.endswith("/endpoints"))):
        raise VerificationError("unsafe_discovery_url")
    try:
        with httpx.Client(follow_redirects=False, timeout=timeout) as session:
            with session.stream("GET", url, headers={"Accept": "application/json"}) as response:
                status = response.status_code
                raw = _read_bounded(response, max_bytes)
    except VerificationError:
        raise
    except (httpx.HTTPError, OSError):
        raise VerificationError("discovery_transport_error") from None
    if status != 200:
        raise VerificationError("model_listing_http_error")
    try:
        return json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        raise VerificationError("invalid_model_listing") from None


class SingleAttemptPost:
    """One bounded POST with no redirect following or implicit retry."""

    def __init__(self, *, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.max_bytes = max_bytes
        self.last_error_type: str | None = None
        self.last_status: int | None = None

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        timeout: float,
    ) -> flp_client.HTTPResult:
        if url != f"{BASE_URL}/chat/completions":
            raise VerificationError("unsafe_completion_url")
        self.last_error_type = None
        self.last_status = None
        try:
            with httpx.Client(follow_redirects=False, timeout=timeout) as session:
                with session.stream("POST", url, headers=headers, json=body) as response:
                    status = response.status_code
                    self.last_status = status
                    raw = _read_bounded(response, self.max_bytes)
        except VerificationError:
            raise
        except (httpx.HTTPError, OSError):
            raise VerificationError("completion_transport_error") from None
        try:
            payload = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("type"), str):
            self.last_error_type = error["type"][:128]
        return flp_client.HTTPResult(status, payload, "")


def _auto_models(provider: Provider) -> list[str]:
    models = [model.name for model in provider.models if model.enabled and model.auto]
    if not models:
        raise VerificationError("no_automatic_models")
    return models


def audit_public_catalog(
    provider: Provider,
    *,
    fetch: FetchFn = _bounded_json_get,
) -> list[dict[str, Any]]:
    """Validate public aggregate and endpoint pricing for every automatic route."""
    _validate_provider(provider)
    payload = fetch(MODELS_URL, timeout=TIMEOUT_SECONDS, max_bytes=MAX_BODY_BYTES)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise VerificationError("invalid_model_listing")
    rows = payload["data"]
    report: list[dict[str, Any]] = []
    for model in _auto_models(provider):
        matches = [row for row in rows if isinstance(row, dict) and row.get("id") == model]
        if len(matches) != 1 or matches[0].get("type") != "language":
            raise VerificationError("invalid_model_listing")
        row = matches[0]
        pricing = row.get("pricing")
        if not isinstance(pricing, dict) or "input" not in pricing or "output" not in pricing:
            raise VerificationError("invalid_model_pricing")
        aggregate_input = _decimal(pricing["input"], "invalid_model_pricing")
        aggregate_output = _decimal(pricing["output"], "invalid_model_pricing")
        _pricing_values(pricing)
        context_window = row.get("context_window")
        max_output = row.get("max_tokens")
        if (
            isinstance(context_window, bool)
            or not isinstance(context_window, int)
            or context_window <= 0
            or isinstance(max_output, bool)
            or not isinstance(max_output, int)
            or max_output <= 0
        ):
            raise VerificationError("invalid_model_listing")

        endpoint_url = f"{MODELS_URL}/{model}/endpoints"
        endpoint_payload = fetch(
            endpoint_url,
            timeout=TIMEOUT_SECONDS,
            max_bytes=MAX_BODY_BYTES,
        )
        data = endpoint_payload.get("data") if isinstance(endpoint_payload, dict) else None
        if (
            not isinstance(data, dict)
            or data.get("id") != model
            or not isinstance(data.get("endpoints"), list)
        ):
            raise VerificationError("invalid_endpoint_listing")
        active = [
            endpoint
            for endpoint in data["endpoints"]
            if isinstance(endpoint, dict) and endpoint.get("status") == 0
        ]
        if not active:
            raise VerificationError("invalid_endpoint_listing")
        endpoint_prices: list[Decimal] = []
        providers: list[str] = []
        try:
            for endpoint in active:
                name = endpoint.get("provider_name")
                if not isinstance(name, str) or not name.strip() or len(name) > 128:
                    raise VerificationError("invalid_endpoint_listing")
                providers.append(name)
                endpoint_prices.extend(_pricing_values(endpoint.get("pricing")))
        except VerificationError as exc:
            if exc.classification == "invalid_model_pricing":
                raise VerificationError("invalid_endpoint_listing") from None
            raise

        zero_price = aggregate_input == 0 and aggregate_output == 0
        if zero_price and any(price != 0 for price in endpoint_prices):
            raise VerificationError("pricing_drift")
        report.append(
            {
                "model": model,
                "zero_price": zero_price,
                "aggregate_input_per_token": _decimal_text(aggregate_input),
                "aggregate_output_per_token": _decimal_text(aggregate_output),
                "max_endpoint_price_per_unit": _decimal_text(max(endpoint_prices)),
                "active_endpoint_count": len(active),
                "active_providers": sorted(providers),
                "context_window": context_window,
                "max_output_tokens": max_output,
            }
        )
    return report


def _failure_classification(status: int, error_type: str | None) -> str:
    if status == 403 and error_type == "customer_verification_required":
        return "customer_verification_required"
    if status in {401, 403}:
        return "auth_required"
    if status == 402:
        return "billing_or_credit"
    if status == 429:
        return "rate_limited"
    return "provider_error"


def _gateway_metadata(raw: Any) -> tuple[str, Decimal]:
    if not isinstance(raw, dict):
        raise VerificationError("provenance_missing")
    metadata = raw.get("provider_metadata") or raw.get("providerMetadata")
    if not isinstance(metadata, dict):
        raise VerificationError("provenance_missing")
    gateway = metadata.get("gateway")
    if not isinstance(gateway, dict):
        raise VerificationError("provenance_missing")
    provider = gateway.get("provider")
    cost = gateway.get("cost")
    if not isinstance(provider, str) or not provider.strip() or len(provider) > 128 or cost is None:
        raise VerificationError("provenance_missing")
    return provider, _decimal(cost, "invalid_returned_cost")


def verify(
    provider: Provider,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    attest_credit_spend: bool = False,
    fetch: FetchFn = _bounded_json_get,
    post: PostFn | None = None,
) -> dict[str, Any]:
    """Run three bounded normal-client canaries after mandatory public preflight."""
    _validate_provider(provider)
    if not isinstance(api_key, str) or not api_key or len(api_key) > 8_192:
        raise VerificationError("auth_missing")
    public_catalog = audit_public_catalog(provider, fetch=fetch)
    selected = next((row for row in public_catalog if row["model"] == model), None)
    if selected is None:
        raise VerificationError("model_not_automatic")
    if not selected["zero_price"] and not attest_credit_spend:
        raise VerificationError("credit_spend_not_attested")
    transport = post if post is not None else SingleAttemptPost()
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, ATTEMPTS + 1):
        try:
            reply = flp_client.call(
                provider,
                model,
                _CANARY,
                api_key=api_key,
                env={"AI_GATEWAY_API_KEY": api_key},
                max_tokens=MAX_TOKENS,
                temperature=0.0,
                timeout=TIMEOUT_SECONDS,
                post=transport,
                enforce_thinking_floor=False,
            )
        except VerificationError:
            raise
        except ProviderHTTPError as exc:
            error_type = getattr(transport, "last_error_type", None)
            if exc.status == 502 and getattr(transport, "last_status", None) == 200:
                raise VerificationError("malformed_completion") from None
            raise VerificationError(_failure_classification(exc.status, error_type)) from None
        except (httpx.HTTPError, OSError):
            raise VerificationError("completion_transport_error") from None
        except Exception:
            raise VerificationError("unexpected_completion_error") from None
        if not reply.text.strip():
            raise VerificationError("empty_completion")
        raw = reply.raw
        returned_model = raw.get("model") if isinstance(raw, dict) else None
        if returned_model != model:
            raise VerificationError("model_mismatch")
        serving_provider, cost = _gateway_metadata(raw)
        if selected["zero_price"] and cost != 0:
            raise VerificationError("nonzero_returned_cost")
        attempts.append(
            {
                "attempt": attempt,
                "status": 200,
                "non_empty": True,
                "returned_model": returned_model,
                "serving_provider": serving_provider,
                "cost": _decimal_text(cost),
                "prompt_tokens": reply.prompt_tokens,
                "completion_tokens": reply.completion_tokens,
            }
        )
    return {
        "schema_version": 1,
        "ok": True,
        "provider": "vercel",
        "model": model,
        "passing": len(attempts),
        "attempts": attempts,
        "public_catalog": public_catalog,
        "credit_spend_attested": bool(attest_credit_spend),
    }


def packaged_vercel_provider() -> Provider:
    providers = load_catalog(PACKAGED_CATALOG)
    matches = [provider for provider in providers if provider.id == "vercel"]
    if len(matches) != 1:
        raise VerificationError("unsafe_provider_configuration")
    provider = matches[0]
    _validate_provider(provider)
    return provider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="automatic Vercel model to probe")
    parser.add_argument(
        "--attest-credit-spend",
        action="store_true",
        help="confirm a Vercel-side budget and permit a priced model canary",
    )
    parser.add_argument(
        "--public-only",
        action="store_true",
        help="validate public pricing/endpoints without using a credential",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        provider = packaged_vercel_provider()
        if args.public_only:
            report: dict[str, Any] = {
                "schema_version": 1,
                "ok": True,
                "provider": "vercel",
                "public_catalog": audit_public_catalog(provider),
            }
        else:
            env = effective_env()
            report = verify(
                provider,
                api_key=env.get("AI_GATEWAY_API_KEY", ""),
                model=args.model,
                attest_credit_spend=args.attest_credit_spend,
            )
    except VerificationError as exc:
        print(json.dumps({"classification": exc.classification, "ok": False}, sort_keys=True))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
