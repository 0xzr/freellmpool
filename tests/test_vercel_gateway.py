from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from freellmpool.client import HTTPResult
from freellmpool.models import Model, Provider

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "verify_vercel_gateway",
    ROOT / "scripts" / "verify_vercel_gateway.py",
)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VERIFIER
SPEC.loader.exec_module(VERIFIER)


AUTO_MODELS = (
    Model("poolside/laguna-s-2.1-free", rpd=0),
    Model("nvidia/nemotron-3.5-lightning-free", rpd=0),
    Model("deepseek/deepseek-v4-flash-0731", rpd=50),
)


def _provider(*, base_url: str = "https://ai-gateway.vercel.sh/v1") -> Provider:
    return Provider(
        id="vercel",
        label="Vercel AI Gateway",
        adapter="openai",
        base_url=base_url,
        key_env="AI_GATEWAY_API_KEY",
        models=AUTO_MODELS + (Model("zai/glm-5.2", auto=False),),
    )


def _model_row(model: str, *, input_price: str, output_price: str) -> dict:
    return {
        "id": model,
        "object": "model",
        "type": "language",
        "context_window": 256_000,
        "max_tokens": 32_768,
        "pricing": {"input": input_price, "output": output_price},
    }


def _endpoint_payload(
    model: str,
    *,
    prompt: str,
    completion: str,
    status: int = 0,
) -> dict:
    return {
        "data": {
            "id": model,
            "endpoints": [
                {
                    "provider_name": model.split("/", 1)[0],
                    "status": status,
                    "pricing": {
                        "prompt": prompt,
                        "completion": completion,
                        "request": "0",
                        "input_cache_read": "0",
                        "prompt_tiers": [{"cost": prompt, "min": 0, "max": 1_000_000}],
                    },
                }
            ],
        }
    }


def _fetcher(
    *,
    models_payload: object | None = None,
    endpoint_overrides: dict[str, object] | None = None,
):
    rows = [
        _model_row(AUTO_MODELS[0].name, input_price="0", output_price="0"),
        _model_row(AUTO_MODELS[1].name, input_price="0", output_price="0"),
        _model_row(AUTO_MODELS[2].name, input_price="0.000000076", output_price="0.000000153"),
    ]
    endpoint_payloads = {
        AUTO_MODELS[0].name: _endpoint_payload(
            AUTO_MODELS[0].name, prompt="0", completion="0"
        ),
        AUTO_MODELS[1].name: _endpoint_payload(
            AUTO_MODELS[1].name, prompt="0", completion="0"
        ),
        AUTO_MODELS[2].name: _endpoint_payload(
            AUTO_MODELS[2].name, prompt="0.00000028", completion="0.00000066"
        ),
    }
    endpoint_payloads.update(endpoint_overrides or {})

    def fetch(url: str, *, timeout: float, max_bytes: int):
        assert timeout <= 20
        assert max_bytes <= 1_000_000
        if url == VERIFIER.MODELS_URL:
            return {"object": "list", "data": rows} if models_payload is None else models_payload
        prefix = VERIFIER.MODELS_URL + "/"
        suffix = "/endpoints"
        assert url.startswith(prefix) and url.endswith(suffix)
        model = url[len(prefix) : -len(suffix)]
        return endpoint_payloads[model]

    return fetch


class _Post:
    def __init__(
        self,
        model: str,
        *,
        text: str = "OK",
        cost: str = "0",
        provider: Any = "poolside",
        status: int = 200,
        error_type: str | None = None,
        usage: Any = None,
    ) -> None:
        self.model = model
        self.text = text
        self.cost = cost
        self.provider = provider
        self.status = status
        self.error_type = error_type
        self.usage = {"prompt_tokens": 5, "completion_tokens": 1} if usage is None else usage
        self.calls: list[tuple[str, dict, dict, float]] = []
        self.last_error_type: str | None = None
        self.last_status: int | None = None

    def __call__(self, url: str, headers: dict, body: dict, timeout: float) -> HTTPResult:
        self.calls.append((url, headers, body, timeout))
        self.last_error_type = self.error_type
        self.last_status = self.status
        if self.status != 200:
            return HTTPResult(
                self.status,
                {"error": {"type": self.error_type, "message": "do-not-serialize-response"}},
                "",
            )
        return HTTPResult(
            200,
            {
                "id": "generation-test",
                "model": self.model,
                "choices": [{"message": {"role": "assistant", "content": self.text}}],
                "usage": self.usage,
                "provider_metadata": {
                    "gateway": {
                        "cost": self.cost,
                        "routing": {"finalProvider": self.provider},
                    }
                },
            },
            "",
        )


def test_public_audit_covers_every_auto_route_and_records_price_maxima():
    report = VERIFIER.audit_public_catalog(_provider(), fetch=_fetcher())

    assert [row["model"] for row in report] == [model.name for model in AUTO_MODELS]
    assert report[0]["zero_price"] is True
    assert report[1]["zero_price"] is True
    assert report[2]["zero_price"] is False
    assert report[2]["aggregate_input_per_token"] == "0.000000076"
    assert report[2]["aggregate_output_per_token"] == "0.000000153"
    assert report[2]["max_endpoint_price_per_unit"] == "0.00000066"
    assert report[2]["active_endpoint_count"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"data": "wrong"},
        {"data": []},
        {"data": [{"id": "poolside/laguna-s-2.1-free", "type": "image", "pricing": {}}]},
    ],
)
def test_public_audit_rejects_malformed_empty_or_wrong_type_listings(payload):
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.audit_public_catalog(_provider(), fetch=_fetcher(models_payload=payload))
    assert exc.value.classification == "invalid_model_listing"


@pytest.mark.parametrize(
    "pricing",
    [
        None,
        {},
        {"input": "free", "output": "0"},
        {"input": "-1", "output": "0"},
        {"input": "1e999999", "output": "0"},
        {"input": "0"},
    ],
)
def test_public_audit_rejects_missing_or_malformed_aggregate_pricing(pricing):
    payload = {
        "data": [
            {
                **_model_row(AUTO_MODELS[0].name, input_price="0", output_price="0"),
                "pricing": pricing,
            },
            _model_row(AUTO_MODELS[1].name, input_price="0", output_price="0"),
            _model_row(AUTO_MODELS[2].name, input_price="1", output_price="1"),
        ]
    }
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.audit_public_catalog(_provider(), fetch=_fetcher(models_payload=payload))
    assert exc.value.classification == "invalid_model_pricing"


@pytest.mark.parametrize(
    "endpoint_payload",
    [
        {},
        {"data": []},
        {"data": {"id": AUTO_MODELS[0].name, "endpoints": []}},
        _endpoint_payload(AUTO_MODELS[0].name, prompt="0", completion="0", status=1),
        _endpoint_payload(AUTO_MODELS[0].name, prompt="bad", completion="0"),
        _endpoint_payload(AUTO_MODELS[0].name, prompt="0", completion="0.01"),
    ],
)
def test_public_audit_rejects_bad_or_nonzero_free_route_endpoints(endpoint_payload):
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.audit_public_catalog(
            _provider(),
            fetch=_fetcher(endpoint_overrides={AUTO_MODELS[0].name: endpoint_payload}),
        )
    assert exc.value.classification in {"invalid_endpoint_listing", "pricing_drift"}


@pytest.mark.parametrize("malformed", ["not-a-row", {"status": "0"}, {"status": True}])
def test_public_audit_rejects_mixed_valid_and_malformed_endpoint_rows(malformed):
    payload = _endpoint_payload(AUTO_MODELS[0].name, prompt="0", completion="0")
    payload["data"]["endpoints"].append(malformed)
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.audit_public_catalog(
            _provider(), fetch=_fetcher(endpoint_overrides={AUTO_MODELS[0].name: payload})
        )
    assert exc.value.classification == "invalid_endpoint_listing"


def test_public_audit_rejects_malformed_tier_cost():
    payload = _endpoint_payload(AUTO_MODELS[0].name, prompt="0", completion="0")
    payload["data"]["endpoints"][0]["pricing"]["prompt_tiers"] = [{"min": 0}]
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.audit_public_catalog(
            _provider(),
            fetch=_fetcher(endpoint_overrides={AUTO_MODELS[0].name: payload}),
        )
    assert exc.value.classification == "invalid_endpoint_listing"


def test_public_audit_rejects_unknown_pricing_fields():
    rows = [
        _model_row(AUTO_MODELS[0].name, input_price="0", output_price="0"),
        _model_row(AUTO_MODELS[1].name, input_price="0", output_price="0"),
        _model_row(AUTO_MODELS[2].name, input_price="1", output_price="1"),
    ]
    rows[0]["pricing"]["surprise_fee"] = "1"

    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.audit_public_catalog(_provider(), fetch=_fetcher(models_payload={"data": rows}))
    assert exc.value.classification == "pricing_schema_drift"


def test_public_audit_accepts_validated_varies_by_provider_metadata():
    rows = [
        _model_row(AUTO_MODELS[0].name, input_price="0", output_price="0"),
        _model_row(AUTO_MODELS[1].name, input_price="0", output_price="0"),
        _model_row(AUTO_MODELS[2].name, input_price="1", output_price="1"),
    ]
    rows[2]["pricing"]["varies_by_provider"] = True

    report = VERIFIER.audit_public_catalog(
        _provider(), fetch=_fetcher(models_payload={"data": rows})
    )
    assert report[2]["zero_price"] is False


def test_public_audit_rejects_malformed_pricing_metadata():
    rows = [
        _model_row(AUTO_MODELS[0].name, input_price="0", output_price="0"),
        _model_row(AUTO_MODELS[1].name, input_price="0", output_price="0"),
        _model_row(AUTO_MODELS[2].name, input_price="1", output_price="1"),
    ]
    rows[2]["pricing"]["varies_by_provider"] = "true"

    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.audit_public_catalog(_provider(), fetch=_fetcher(models_payload={"data": rows}))
    assert exc.value.classification == "invalid_model_pricing"


def test_public_audit_accepts_bounded_regional_peak_pricing_and_current_provider():
    rows = [
        _model_row(AUTO_MODELS[0].name, input_price="0", output_price="0"),
        _model_row(AUTO_MODELS[1].name, input_price="0", output_price="0"),
        _model_row(AUTO_MODELS[2].name, input_price="0.000000076", output_price="0.000000153"),
    ]
    rows[2]["pricing"]["regional"] = {
        "us": {
            "input": "0.00000013",
            "output": "0.00000026",
            "input_cache_read": "0.000000028",
        }
    }
    endpoint = _endpoint_payload(
        AUTO_MODELS[2].name,
        prompt="0.00000022",
        completion="0.00000066",
    )
    endpoint["data"]["endpoints"][0]["provider_name"] = "digitalocean"
    endpoint["data"]["endpoints"][0]["pricing"]["peak_pricing"] = {
        "multiplier": 2,
        "windows": [
            {
                "start_minute_utc": 60,
                "end_minute_utc": 240,
                "days_of_week": [1, 2, 3, 4, 5],
            }
        ],
    }

    report = VERIFIER.audit_public_catalog(
        _provider(),
        fetch=_fetcher(
            models_payload={"data": rows},
            endpoint_overrides={AUTO_MODELS[2].name: endpoint},
        ),
    )

    assert report[2]["zero_price"] is False
    assert report[2]["max_endpoint_price_per_unit"] == "0.00000132"
    assert report[2]["active_providers"] == ["digitalocean"]


@pytest.mark.parametrize(
    "regional",
    [
        [],
        {},
        {"US": {"input": "0", "output": "0"}},
        {"us": {"input": "0"}},
        {"us": {"input": "0", "output": "0", "surprise_fee": "1"}},
    ],
)
def test_public_audit_rejects_malformed_regional_pricing(regional):
    rows = [
        _model_row(AUTO_MODELS[0].name, input_price="0", output_price="0"),
        _model_row(AUTO_MODELS[1].name, input_price="0", output_price="0"),
        _model_row(AUTO_MODELS[2].name, input_price="1", output_price="1"),
    ]
    rows[2]["pricing"]["regional"] = regional

    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.audit_public_catalog(_provider(), fetch=_fetcher(models_payload={"data": rows}))
    assert exc.value.classification in {"invalid_model_pricing", "pricing_schema_drift"}


@pytest.mark.parametrize(
    "peak_pricing",
    [
        {},
        {"multiplier": 0, "windows": []},
        {"multiplier": 2, "windows": []},
        {
            "multiplier": 2,
            "windows": [
                {"start_minute_utc": 240, "end_minute_utc": 60, "days_of_week": [1]}
            ],
        },
        {
            "multiplier": 2,
            "windows": [
                {"start_minute_utc": 0, "end_minute_utc": 60, "days_of_week": [7]}
            ],
        },
    ],
)
def test_public_audit_rejects_malformed_peak_pricing(peak_pricing):
    endpoint = _endpoint_payload(AUTO_MODELS[0].name, prompt="0", completion="0")
    endpoint["data"]["endpoints"][0]["pricing"]["peak_pricing"] = peak_pricing

    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.audit_public_catalog(
            _provider(),
            fetch=_fetcher(endpoint_overrides={AUTO_MODELS[0].name: endpoint}),
        )
    assert exc.value.classification == "invalid_endpoint_listing"


def test_aggregate_request_fee_prevents_zero_price_classification():
    rows = [
        _model_row(AUTO_MODELS[0].name, input_price="0", output_price="0"),
        _model_row(AUTO_MODELS[1].name, input_price="0", output_price="0"),
        _model_row(AUTO_MODELS[2].name, input_price="1", output_price="1"),
    ]
    rows[0]["pricing"]["request"] = "1"

    report = VERIFIER.audit_public_catalog(
        _provider(), fetch=_fetcher(models_payload={"data": rows})
    )
    assert report[0]["zero_price"] is False


def test_endpoint_pricing_requires_prompt_and_completion():
    payload = _endpoint_payload(AUTO_MODELS[0].name, prompt="0", completion="0")
    payload["data"]["endpoints"][0]["pricing"] = {"request": "0"}

    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.audit_public_catalog(
            _provider(),
            fetch=_fetcher(endpoint_overrides={AUTO_MODELS[0].name: payload}),
        )
    assert exc.value.classification == "invalid_endpoint_listing"


@pytest.mark.parametrize(
    "tiers",
    [
        [{"cost": "0"}],
        [{"cost": "0", "min": -1}],
        [{"cost": "0", "min": 10, "max": 10}],
        [{"cost": "0", "min": 10, "max": 5}],
        [{"cost": "0", "min": 10, "unexpected": 20}],
        [{"cost": "0", "min": 0}, {"cost": "0", "min": 10, "max": 20}],
        [
            {"cost": "0", "min": 0, "max": 20},
            {"cost": "0", "min": 10, "max": 30},
        ],
    ],
)
def test_public_audit_rejects_malformed_tier_bounds(tiers):
    payload = _endpoint_payload(AUTO_MODELS[0].name, prompt="0", completion="0")
    payload["data"]["endpoints"][0]["pricing"]["prompt_tiers"] = tiers
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.audit_public_catalog(
            _provider(), fetch=_fetcher(endpoint_overrides={AUTO_MODELS[0].name: payload})
        )
    assert exc.value.classification == "invalid_endpoint_listing"


def test_decimal_rejects_extreme_zero_exponent_without_expanding_output():
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER._decimal("0E-100000", "invalid_model_pricing")
    assert exc.value.classification == "invalid_model_pricing"
    assert VERIFIER._decimal_text(VERIFIER._decimal("0E-9", "bad")) == "0"


def test_acceptance_runs_exactly_three_single_attempt_normal_client_calls():
    post = _Post(AUTO_MODELS[0].name)
    report = VERIFIER.verify(
        _provider(),
        api_key="super-secret-vck-token",
        fetch=_fetcher(),
        post=post,
    )

    assert report["ok"] is True
    assert report["passing"] == 3
    assert len(report["attempts"]) == 3
    assert len(post.calls) == 3
    for url, headers, body, timeout in post.calls:
        assert url == "https://ai-gateway.vercel.sh/v1/chat/completions"
        assert headers["Authorization"].startswith("Bearer ")
        assert body["model"] == AUTO_MODELS[0].name
        assert body["max_tokens"] == 8
        assert timeout <= 20
    serialized = json.dumps(report)
    assert "super-secret" not in serialized
    assert "OK" not in serialized


def test_bounded_reader_rejects_oversized_response():
    class Response:
        headers = {}

        def iter_raw(self):
            yield b"1234"
            yield b"5678"

    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER._read_bounded(Response(), 7, deadline=10, now=lambda: 0)
    assert exc.value.classification == "response_too_large"


def test_bounded_reader_enforces_total_deadline():
    class Response:
        headers = {}

        def iter_raw(self):
            yield b"x"
            yield b"y"

    ticks = iter([0.0, 2.0])
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER._read_bounded(Response(), 100, deadline=1.0, now=lambda: next(ticks))
    assert exc.value.classification == "request_deadline_exceeded"


def test_bounded_reader_rejects_content_encoding_before_decompression():
    iterated = False

    class Response:
        headers = {"Content-Encoding": "gzip"}

        def iter_raw(self):
            nonlocal iterated
            iterated = True
            yield b"compressed"

    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER._read_bounded(Response(), 100, deadline=1, now=lambda: 0)
    assert exc.value.classification == "unexpected_content_encoding"
    assert iterated is False


def test_network_helpers_reject_unpinned_urls_before_transport(monkeypatch):
    called = False

    class Client:
        def __init__(self, **_kwargs):
            nonlocal called
            called = True

    monkeypatch.setattr(VERIFIER.httpx, "Client", Client)
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER._bounded_json_get("https://evil.example/models")
    assert exc.value.classification == "unsafe_discovery_url"
    assert called is False

    post = VERIFIER.SingleAttemptPost()
    with pytest.raises(VERIFIER.VerificationError) as exc:
        post("https://evil.example/chat/completions", {}, {}, 1)
    assert exc.value.classification == "unsafe_completion_url"
    assert called is False


def test_network_helpers_disable_redirect_following(monkeypatch):
    seen = []
    requests = []

    class Response:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_raw(self):
            yield b'{"data": []}'

    class Client:
        def __init__(self, **kwargs):
            seen.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, *args, **kwargs):
            requests.append((args, kwargs))
            return Response()

    monkeypatch.setattr(VERIFIER.httpx, "Client", Client)
    assert VERIFIER._bounded_json_get(VERIFIER.MODELS_URL) == {"data": []}
    assert seen == [{"follow_redirects": False, "timeout": VERIFIER.TIMEOUT_SECONDS}]
    assert len(requests) == 1
    assert requests[0][1]["headers"]["Accept-Encoding"] == "identity"


def test_completion_transport_disables_redirects_and_posts_once(monkeypatch):
    seen_clients = []
    seen_streams = []

    class Response:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_raw(self):
            yield b'{"choices": []}'

    class Client:
        def __init__(self, **kwargs):
            seen_clients.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, method, url, **kwargs):
            seen_streams.append((method, url, kwargs))
            return Response()

    monkeypatch.setattr(VERIFIER.httpx, "Client", Client)
    result = VERIFIER.SingleAttemptPost()(
        f"{VERIFIER.BASE_URL}/chat/completions", {}, {"model": AUTO_MODELS[0].name}, 3
    )
    assert result.status == 200
    assert seen_clients == [{"follow_redirects": False, "timeout": 3}]
    assert len(seen_streams) == 1
    assert seen_streams[0][0:2] == ("POST", f"{VERIFIER.BASE_URL}/chat/completions")
    assert seen_streams[0][2]["headers"]["Accept-Encoding"] == "identity"


def test_model_listing_http_status_is_classified_and_sanitized(monkeypatch):
    class Response:
        status_code = 402
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_raw(self):
            yield b'{"error":"do-not-serialize"}'

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(VERIFIER.httpx, "Client", Client)
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER._bounded_json_get(VERIFIER.MODELS_URL)
    assert exc.value.classification == "billing_or_credit"
    assert "do-not-serialize" not in str(exc.value)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"n":' + (b"9" * 5_000) + b"}",
    ],
)
def test_model_listing_malformed_json_failures_are_sanitized(monkeypatch, raw):
    class Response:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_raw(self):
            yield raw

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(VERIFIER.httpx, "Client", Client)
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER._bounded_json_get(VERIFIER.MODELS_URL)
    assert exc.value.classification == "invalid_model_listing"


def test_json_recursion_error_is_sanitized(monkeypatch):
    monkeypatch.setattr(
        VERIFIER.json,
        "loads",
        lambda _raw: (_ for _ in ()).throw(RecursionError("do-not-serialize")),
    )
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER._json_loads(b"[]", "invalid_model_listing")
    assert exc.value.classification == "invalid_model_listing"
    assert "do-not-serialize" not in str(exc.value)


def test_verifier_rejects_user_catalog_redirect_and_missing_key_before_fetch():
    calls = []
    fetch = lambda *_args, **_kwargs: calls.append(True)  # noqa: E731
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.verify(_provider(base_url="https://evil.example/v1"), api_key="secret", fetch=fetch)
    assert exc.value.classification == "unsafe_provider_configuration"
    assert calls == []

    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.verify(_provider(), api_key="", fetch=fetch)
    assert exc.value.classification == "auth_missing"
    assert calls == []


def test_priced_model_requires_explicit_credit_attestation_before_post():
    post = _Post(AUTO_MODELS[2].name, cost="0.000001")
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.verify(
            _provider(),
            api_key="secret",
            model=AUTO_MODELS[2].name,
            fetch=_fetcher(),
            post=post,
        )
    assert exc.value.classification == "credit_spend_not_attested"
    assert post.calls == []


def test_priced_model_canary_records_cost_after_attestation():
    post = _Post(AUTO_MODELS[2].name, cost="0.000001", provider="deepseek")
    report = VERIFIER.verify(
        _provider(),
        api_key="secret",
        model=AUTO_MODELS[2].name,
        attest_credit_spend=True,
        fetch=_fetcher(),
        post=post,
    )
    assert report["ok"] is True
    assert report["attempts"][0]["cost"] == "0.000001"
    assert report["attempts"][0]["serving_provider"] == "deepseek"


@pytest.mark.parametrize(
    ("status", "error_type", "classification"),
    [
        (401, "authentication_error", "auth_required"),
        (403, "customer_verification_required", "customer_verification_required"),
        (403, "forbidden", "auth_required"),
        (402, "insufficient_credits", "billing_or_credit"),
        (429, "rate_limit_exceeded", "rate_limited"),
        (500, "upstream_error", "provider_error"),
    ],
)
def test_failures_are_status_classified_and_sanitized(status, error_type, classification):
    post = _Post(AUTO_MODELS[0].name, status=status, error_type=error_type)
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.verify(
            _provider(),
            api_key="secret",
            fetch=_fetcher(),
            post=post,
        )
    assert exc.value.classification == classification
    assert len(post.calls) == 1
    assert "do-not-serialize" not in str(exc.value)


def test_malformed_http_success_is_distinct_and_sanitized():
    class Post:
        last_error_type = None
        last_status = 200

        def __init__(self):
            self.calls = 0

        def __call__(self, *_args, **_kwargs):
            self.calls += 1
            return HTTPResult(200, {"unexpected": "do-not-serialize"}, "")

    post = Post()
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.verify(
            _provider(),
            api_key="secret",
            fetch=_fetcher(),
            post=post,
        )
    assert exc.value.classification == "malformed_completion"
    assert post.calls == 1
    assert "do-not-serialize" not in str(exc.value)


@pytest.mark.parametrize(
    ("text", "returned_model", "cost", "provider", "classification"),
    [
        ("", AUTO_MODELS[0].name, "0", "poolside", "empty_completion"),
        ("OK", "wrong/model", "0", "poolside", "model_mismatch"),
        ("OK", AUTO_MODELS[0].name, None, "poolside", "provenance_missing"),
        ("OK", AUTO_MODELS[0].name, "bad", "poolside", "invalid_returned_cost"),
        ("OK", AUTO_MODELS[0].name, "0.01", "poolside", "nonzero_returned_cost"),
        ("OK", AUTO_MODELS[0].name, "0", "", "provenance_missing"),
        ("OK", AUTO_MODELS[0].name, "0", "not-audited", "unverified_serving_provider"),
        ("OK", AUTO_MODELS[0].name, "0", {"secret": "do-not-serialize"}, "provenance_missing"),
    ],
)
def test_success_shape_must_be_nonempty_exact_zero_cost_and_provenanced(
    text, returned_model, cost, provider, classification
):
    post = _Post(returned_model, text=text, cost=cost, provider=provider)
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.verify(
            _provider(),
            api_key="secret",
            fetch=_fetcher(),
            post=post,
        )
    assert exc.value.classification == classification
    assert "do-not-serialize" not in str(exc.value)


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": {"secret": "do-not-serialize"}, "completion_tokens": 1},
        {"prompt_tokens": True, "completion_tokens": 1},
        {"prompt_tokens": -1, "completion_tokens": 1},
        {"prompt_tokens": 1_000_000_001, "completion_tokens": 1},
    ],
)
def test_success_report_rejects_hostile_or_unbounded_usage(usage):
    post = _Post(AUTO_MODELS[0].name, usage=usage)
    with pytest.raises(VERIFIER.VerificationError) as exc:
        VERIFIER.verify(_provider(), api_key="secret", fetch=_fetcher(), post=post)
    assert exc.value.classification == "invalid_usage"
    assert "do-not-serialize" not in str(exc.value)


def test_packaged_provider_ignores_user_catalog_override(monkeypatch, tmp_path):
    evil = tmp_path / "providers.toml"
    evil.write_text(
        '[[provider]]\nid="vercel"\nlabel="evil"\nadapter="openai"\n'
        'base_url="https://evil.example/v1"\nkey_env="AI_GATEWAY_API_KEY"\nmodels=[]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("FREELLMPOOL_CONFIG", str(evil))
    assert VERIFIER.packaged_vercel_provider().base_url == VERIFIER.BASE_URL


def test_cli_error_output_is_allowlisted_and_contains_no_sensitive_content(monkeypatch, capsys):
    monkeypatch.setattr(VERIFIER, "packaged_vercel_provider", lambda: _provider())
    monkeypatch.setattr(VERIFIER, "verify", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        VERIFIER.VerificationError("customer_verification_required")
    ))
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "secret-vck")

    assert VERIFIER.main([]) == 1
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload == {"classification": "customer_verification_required", "ok": False}
    assert output.err == ""
    assert "secret-vck" not in output.out


def test_cli_unexpected_error_is_sanitized(monkeypatch, capsys):
    monkeypatch.setattr(
        VERIFIER,
        "packaged_vercel_provider",
        lambda: (_ for _ in ()).throw(RuntimeError("do-not-serialize")),
    )
    assert VERIFIER.main([]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "classification": "unexpected_verifier_error",
        "ok": False,
    }
