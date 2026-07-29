from __future__ import annotations

import asyncio
import base64
import binascii
import json
import struct
import threading
import urllib.request
import zlib
from pathlib import Path

import pytest
from helpers import make_post

import freellmpool.conformance as conformance_module
from freellmpool.aio import AsyncPool
from freellmpool.client import HTTPResult
from freellmpool.conformance import (
    _RED_PIXEL,
    FEATURE_CHAT,
    FEATURE_JSON,
    FEATURE_JSON_SCHEMA,
    FEATURE_RESPONSES,
    FEATURE_STREAMING,
    FEATURE_TOOLS,
    FEATURE_VISION,
    STATUS_PASS,
    STATUS_UNSUPPORTED,
    ConformanceStore,
    classify_canary_exception,
    required_features,
    run_target_canaries,
    validate_canary_result,
)
from freellmpool.errors import NoProvidersConfigured, ProviderHTTPError
from freellmpool.models import Model, Provider, Reply
from freellmpool.proxy import (
    _anthropic_models_payload,
    _openai_models_payload,
    _responses_input_to_messages,
    _status_payload,
    serve,
)
from freellmpool.quota import QuotaStore
from freellmpool.router import Pool


def _provider(provider_id: str, adapter: str = "openai") -> Provider:
    return Provider(
        id=provider_id,
        label=provider_id,
        adapter=adapter,
        base_url=f"https://{provider_id}.test/v1",
        auth="none",
        models=(Model("model-1"),),
    )


def test_store_persists_only_bounded_machine_readable_evidence(tmp_path):
    path = tmp_path / "conformance.json"
    provider = _provider("alpha")
    store = ConformanceStore(path)

    store.record(
        provider,
        "model-1",
        FEATURE_TOOLS,
        status=STATUS_PASS,
        classification="verified",
        verified_at="2026-07-29T12:00:00Z",
    )

    loaded = ConformanceStore(path)
    evidence = loaded.evidence(provider, "model-1")
    assert evidence[FEATURE_TOOLS]["status"] == STATUS_PASS
    assert evidence[FEATURE_TOOLS]["verification_count"] == 1
    serialized = path.read_text(encoding="utf-8")
    assert "response" not in serialized
    assert "prompt" not in serialized
    assert "secret" not in serialized
    assert len(serialized) < 16_384


def test_store_invalidates_evidence_after_adapter_or_model_identity_change(tmp_path):
    path = tmp_path / "conformance.json"
    original = _provider("alpha", "openai")
    store = ConformanceStore(path)
    store.record(
        original,
        "model-1",
        FEATURE_STREAMING,
        status=STATUS_PASS,
        classification="verified",
    )

    changed_adapter = _provider("alpha", "gemini")
    assert store.evidence(changed_adapter, "model-1") == {}
    assert store.evidence(original, "renamed-model") == {}


def test_store_rejects_oversized_or_malformed_state_without_mutating_it(tmp_path):
    path = tmp_path / "conformance.json"
    path.write_text("{" + ("x" * 2_100_000), encoding="utf-8")

    store = ConformanceStore(path)

    assert store.snapshot() == {"version": 1, "targets": {}}
    assert path.stat().st_size > 2_000_000


def test_store_fails_closed_on_deeply_nested_json(tmp_path):
    path = tmp_path / "conformance.json"
    original = ("[" * 10_000) + "0" + ("]" * 10_000)
    path.write_text(original, encoding="utf-8")

    assert ConformanceStore(path).snapshot() == {"version": 1, "targets": {}}
    assert path.read_text(encoding="utf-8") == original


def test_store_evicts_oldest_target_at_bounded_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(conformance_module, "_MAX_TARGETS", 2)
    store = ConformanceStore(tmp_path / "conformance.json")
    alpha = _provider("alpha")
    beta = _provider("beta")
    gamma = _provider("gamma")
    for provider, stamp in (
        (alpha, "2026-07-29T10:00:00Z"),
        (beta, "2026-07-29T11:00:00Z"),
        (gamma, "2026-07-29T12:00:00Z"),
    ):
        store.record(
            provider,
            "model-1",
            FEATURE_CHAT,
            status=STATUS_PASS,
            classification="verified",
            verified_at=stamp,
        )

    snapshot = store.snapshot()
    assert sorted(snapshot["targets"]) == ["beta/model-1", "gamma/model-1"]
    assert store.evidence(alpha, "model-1") == {}
    assert store.evidence(beta, "model-1")[FEATURE_CHAT]["status"] == STATUS_PASS
    assert store.evidence(gamma, "model-1")[FEATURE_CHAT]["status"] == STATUS_PASS


def test_store_uses_windows_cross_process_lock_when_fcntl_is_unavailable(
    tmp_path, monkeypatch
):
    calls = []

    class FakeMsvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd, mode, size):
            calls.append((mode, size))

    monkeypatch.setattr(conformance_module, "fcntl", None)
    monkeypatch.setattr(conformance_module, "msvcrt", FakeMsvcrt, raising=False)
    store = ConformanceStore(tmp_path / "conformance.json")

    with store._file_lock():
        assert calls == [(FakeMsvcrt.LK_LOCK, 1)]

    assert calls == [
        (FakeMsvcrt.LK_LOCK, 1),
        (FakeMsvcrt.LK_UNLCK, 1),
    ]


def test_store_fails_closed_without_cross_process_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(conformance_module, "fcntl", None)
    monkeypatch.setattr(conformance_module, "msvcrt", None, raising=False)
    store = ConformanceStore(tmp_path / "conformance.json")

    with pytest.raises(RuntimeError, match="cross-process"):
        store.record(
            _provider("alpha"),
            "model-1",
            FEATURE_CHAT,
            status=STATUS_PASS,
            classification="verified",
        )


def test_required_features_detects_tools_json_stream_and_vision():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is shown?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ],
        }
    ]
    features = required_features(
        messages,
        tools=[{"type": "function", "function": {"name": "answer"}}],
        response_format={"type": "json_object"},
        stream=True,
    )

    assert features == frozenset({"vision", FEATURE_TOOLS, FEATURE_JSON, FEATURE_STREAMING})


def test_plain_text_response_format_does_not_require_json_conformance(tmp_path):
    provider = _provider("alpha")
    store = ConformanceStore(tmp_path / "conformance.json")
    seen = []

    def post(url, headers, body, timeout):
        seen.append(body)
        return HTTPResult(
            status=200,
            body={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
            text="",
        )

    assert (
        required_features(
            [{"role": "user", "content": "plain"}],
            response_format={"type": "text"},
        )
        == frozenset()
    )
    assert required_features(
        [{"role": "user", "content": "use a tool"}],
        tools=[{"type": "function", "function": {"name": "answer"}}],
        protocol=FEATURE_RESPONSES,
    ) == frozenset({FEATURE_RESPONSES, FEATURE_TOOLS})
    pool = Pool([provider], env={}, post=post, conformance=store)
    reply = pool.chat(
        [{"role": "user", "content": "plain"}],
        response_format={"type": "text"},
    )

    assert reply.provider_id == "alpha"
    assert seen[0]["response_format"] == {"type": "text"}


def test_json_schema_requires_distinct_verified_evidence(tmp_path):
    provider = _provider("alpha")
    store = ConformanceStore(tmp_path / "conformance.json")
    store.record(
        provider,
        "model-1",
        FEATURE_JSON,
        status=STATUS_PASS,
        classification="verified",
    )
    post = make_post({})
    pool = Pool([provider], env={}, post=post, conformance=store)
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
        },
    }

    with pytest.raises(NoProvidersConfigured):
        pool.chat(
            [{"role": "user", "content": "json"}],
            response_format=response_format,
        )
    assert post.calls == []

    store.record(
        provider,
        "model-1",
        FEATURE_JSON_SCHEMA,
        status=STATUS_PASS,
        classification="verified",
    )
    assert (
        pool.chat(
            [{"role": "user", "content": "json"}],
            response_format=response_format,
        ).provider_id
        == "alpha"
    )


def test_async_plain_text_response_format_does_not_require_json_conformance(tmp_path):
    provider = _provider("alpha")
    store = ConformanceStore(tmp_path / "conformance.json")
    seen = []

    async def apost(url, headers, body, timeout):
        seen.append(body)
        return HTTPResult(
            status=200,
            body={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
            text="",
        )

    pool = AsyncPool(
        Pool(
            [provider],
            env={},
            quota=QuotaStore(path=tmp_path / "quota.json"),
            post=make_post({}),
            conformance=store,
        ),
        apost=apost,
    )
    reply = asyncio.run(
        pool.achat(
            [{"role": "user", "content": "plain"}],
            response_format={"type": "text"},
        )
    )

    assert reply.provider_id == "alpha"
    assert seen[0]["response_format"] == {"type": "text"}


def test_proxy_plain_text_response_format_does_not_require_json_conformance(tmp_path):
    provider = _provider("alpha")
    store = ConformanceStore(tmp_path / "conformance.json")
    seen = []

    def post(url, headers, body, timeout):
        seen.append(body)
        return HTTPResult(
            status=200,
            body={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
            text="",
        )

    pool = Pool([provider], env={}, post=post, conformance=store)
    httpd = serve(pool, host="127.0.0.1", port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    request = urllib.request.Request(
        base + "/v1/chat/completions",
        data=json.dumps(
            {
                "model": "auto",
                "messages": [{"role": "user", "content": "plain"}],
                "response_format": {"type": "text"},
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310 - local fixture
            assert response.status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert seen[0]["response_format"] == {"type": "text"}


def test_responses_input_image_is_preserved_and_requires_vision():
    image_url = "data:image/png;base64,AA=="
    messages = _responses_input_to_messages(
        {
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Describe it."},
                        {"type": "input_image", "image_url": image_url},
                    ],
                }
            ]
        }
    )

    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe it."},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]
    assert required_features(messages, protocol=FEATURE_RESPONSES) == frozenset(
        {FEATURE_RESPONSES, FEATURE_VISION}
    )


def test_fixed_vision_canary_is_a_valid_red_rgba_png():
    encoded = _RED_PIXEL.removeprefix("data:image/png;base64,")
    raw = base64.b64decode(encoded, validate=True)
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    chunks = {}
    while offset < len(raw):
        length = struct.unpack(">I", raw[offset : offset + 4])[0]
        kind = raw[offset + 4 : offset + 8]
        data = raw[offset + 8 : offset + 8 + length]
        crc = struct.unpack(">I", raw[offset + 8 + length : offset + 12 + length])[0]
        assert binascii.crc32(kind + data) & 0xFFFFFFFF == crc
        chunks.setdefault(kind, b"")
        chunks[kind] += data
        offset += 12 + length

    width, height, depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", chunks[b"IHDR"]
    )
    assert (width, height, depth, color_type, compression, filter_method, interlace) == (
        1,
        1,
        8,
        6,
        0,
        0,
        0,
    )
    assert zlib.decompress(chunks[b"IDAT"]) == b"\x00\xff\x00\x00\xff"


def test_router_prefers_only_verified_feature_targets_once_evidence_exists(tmp_path):
    alpha = _provider("alpha")
    beta = _provider("beta")
    store = ConformanceStore(tmp_path / "conformance.json")
    store.record(
        alpha,
        "model-1",
        FEATURE_TOOLS,
        status=STATUS_UNSUPPORTED,
        classification="unsupported",
    )
    store.record(
        beta,
        "model-1",
        FEATURE_TOOLS,
        status=STATUS_PASS,
        classification="verified",
    )
    post = make_post({})
    pool = Pool([alpha, beta], env={}, post=post, conformance=store)

    reply = pool.chat(
        [{"role": "user", "content": "call answer"}],
        tools=[{"type": "function", "function": {"name": "answer", "parameters": {}}}],
    )

    assert reply.provider_id == "beta"
    assert "beta.test" in post.calls[0]["url"]


def test_router_preserves_exact_user_pin_even_when_feature_is_unverified(tmp_path):
    alpha = _provider("alpha")
    beta = _provider("beta")
    store = ConformanceStore(tmp_path / "conformance.json")
    store.record(
        beta,
        "model-1",
        FEATURE_TOOLS,
        status=STATUS_PASS,
        classification="verified",
    )
    post = make_post({})
    pool = Pool([alpha, beta], env={}, post=post, conformance=store)

    reply = pool.chat(
        [{"role": "user", "content": "call answer"}],
        model="model-1",
        providers=["alpha"],
        tools=[{"type": "function", "function": {"name": "answer", "parameters": {}}}],
    )

    assert reply.provider_id == "alpha"


def test_async_router_uses_only_verified_tool_targets(tmp_path):
    alpha = _provider("alpha")
    beta = _provider("beta")
    store = ConformanceStore(tmp_path / "conformance.json")
    store.record(
        beta,
        "model-1",
        FEATURE_TOOLS,
        status=STATUS_PASS,
        classification="verified",
    )
    calls = []

    async def apost(url, headers, body, timeout):
        calls.append(url)
        return HTTPResult(
            status=200,
            body={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
            text="",
        )

    pool = AsyncPool(
        Pool(
            [alpha, beta],
            env={},
            quota=QuotaStore(path=tmp_path / "quota.json"),
            post=make_post({}),
            conformance=store,
        ),
        apost=apost,
    )

    reply = asyncio.run(
        pool.achat(
            [{"role": "user", "content": "call answer"}],
            tools=[{"type": "function", "function": {"name": "answer", "parameters": {}}}],
        )
    )

    assert reply.provider_id == "beta"
    assert calls == ["https://beta.test/v1/chat/completions"]


def test_async_router_forwards_and_gates_structured_protocol_requests(tmp_path):
    alpha = _provider("alpha")
    beta = _provider("beta")
    store = ConformanceStore(tmp_path / "conformance.json")
    for feature in (FEATURE_JSON, FEATURE_RESPONSES):
        store.record(
            beta,
            "model-1",
            feature,
            status=STATUS_PASS,
            classification="verified",
        )
    bodies = []

    async def apost(url, headers, body, timeout):
        bodies.append(body)
        return HTTPResult(
            status=200,
            body={"choices": [{"message": {"role": "assistant", "content": '{"ok":true}'}}]},
            text="",
        )

    pool = AsyncPool(
        Pool(
            [alpha, beta],
            env={},
            quota=QuotaStore(path=tmp_path / "quota.json"),
            post=make_post({}),
            conformance=store,
        ),
        apost=apost,
    )

    reply = asyncio.run(
        pool.achat(
            [{"role": "user", "content": "json"}],
            response_format={"type": "json_object"},
            protocol=FEATURE_RESPONSES,
        )
    )

    assert reply.provider_id == "beta"
    assert bodies[0]["response_format"] == {"type": "json_object"}


def test_router_rejects_unverified_feature_requests_without_an_exact_pin(tmp_path):
    alpha = _provider("alpha")
    store = ConformanceStore(tmp_path / "conformance.json")
    post = make_post({})
    pool = Pool([alpha], env={}, post=post, conformance=store)

    with pytest.raises(NoProvidersConfigured):
        pool.chat(
            [{"role": "user", "content": "call answer"}],
            tools=[{"type": "function", "function": {"name": "answer", "parameters": {}}}],
        )
    assert post.calls == []


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ProviderHTTPError(400, "tools unsupported", retryable=True), "unsupported"),
        (ProviderHTTPError(429, "rate limited", retryable=True), "rate_limit"),
        (ProviderHTTPError(503, "down", retryable=True), "availability"),
        (TimeoutError("contains-sensitive-upstream-text"), "timeout"),
    ],
)
def test_canary_failure_classification_is_privacy_safe(exc, expected):
    assert classify_canary_exception(exc) == expected
    assert "sensitive" not in classify_canary_exception(exc)


def test_tool_canary_rejects_semantically_wrong_arguments():
    reply = Reply(
        text="",
        provider_id="alpha",
        model="model-1",
        raw={},
        message={
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "record_number", "arguments": '{"number": 8}'},
                }
            ]
        },
    )

    assert validate_canary_result(FEATURE_TOOLS, reply) == "semantic_mismatch"


def test_json_canary_rejects_valid_json_with_wrong_semantics():
    reply = Reply(
        text='{"ok": false}',
        provider_id="alpha",
        model="model-1",
        raw={},
    )

    assert validate_canary_result(FEATURE_JSON, reply) == "semantic_mismatch"


def test_chat_canary_accepts_normalized_exact_result():
    reply = Reply(text="  OK. \n", provider_id="alpha", model="model-1", raw={})

    assert validate_canary_result(FEATURE_CHAT, reply) == "verified"


def test_vision_canary_requires_the_exact_expected_color_word():
    reply = Reply(
        text="The image is not red",
        provider_id="alpha",
        model="model-1",
        raw={},
    )

    assert validate_canary_result(FEATURE_VISION, reply) == "semantic_mismatch"
    reply.text = "red"
    assert validate_canary_result(FEATURE_VISION, reply) == "verified"


def test_state_json_never_contains_provider_response_or_exception_text(tmp_path):
    provider = _provider("alpha")
    path = tmp_path / "conformance.json"
    store = ConformanceStore(path)
    store.record(
        provider,
        "model-1",
        FEATURE_JSON,
        status="fail",
        classification="semantic_mismatch",
    )

    parsed = json.loads(path.read_text(encoding="utf-8"))
    feature = parsed["targets"]["alpha/model-1"]["features"][FEATURE_JSON]
    assert set(feature) == {
        "status",
        "classification",
        "verified_at",
        "verification_count",
    }


def test_protocol_conformance_operator_contract_is_documented():
    root = Path(__file__).resolve().parents[1]
    doc = (root / "docs" / "PROTOCOL_CONFORMANCE.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    contributing = (root / "CONTRIBUTING.md").read_text(encoding="utf-8")

    for phrase in (
        "freellmpool conformance run",
        "freellmpool conformance status",
        "explicitly pinned",
        "FREELLMPOOL_CONFORMANCE_FILE",
        "FREELLMPOOL_CONFORMANCE_KEYS_JSON",
        "16",
        "never",
    ):
        assert phrase in doc
    assert "docs/PROTOCOL_CONFORMANCE.md" in readme
    assert "docs/PROTOCOL_CONFORMANCE.md" in contributing


def test_default_pool_honors_programmatic_conformance_path(tmp_path, monkeypatch):
    path = tmp_path / "custom-conformance.json"
    monkeypatch.delenv("FREELLMPOOL_CONFORMANCE_FILE", raising=False)

    pool = Pool.from_default_config(
        env={"FREELLMPOOL_CONFORMANCE_FILE": str(path)}
    )

    assert pool.conformance is not None
    assert pool.conformance.path == path


def test_status_and_model_payloads_expose_feature_evidence(tmp_path):
    provider = _provider("alpha")
    store = ConformanceStore(tmp_path / "conformance.json")
    store.record(
        provider,
        "model-1",
        FEATURE_STREAMING,
        status=STATUS_PASS,
        classification="verified",
    )
    pool = Pool([provider], env={}, post=make_post({}), conformance=store)

    status = _status_payload(pool, [])
    model_status = status["providers"][0]["models"][0]
    assert model_status["capabilities"][FEATURE_STREAMING]["status"] == STATUS_PASS
    assert model_status["verified_features"] == [FEATURE_STREAMING]

    models = _openai_models_payload(pool)
    target = next(row for row in models["data"] if row["id"] == "alpha/model-1")
    assert target["capabilities"][FEATURE_STREAMING]["status"] == STATUS_PASS
    assert target["verified_features"] == [FEATURE_STREAMING]

    anthropic = _anthropic_models_payload(pool)
    anthropic_target = next(
        row for row in anthropic["data"] if row["id"] == "alpha/model-1"
    )
    assert anthropic_target["capabilities"][FEATURE_STREAMING]["status"] == STATUS_PASS
    assert anthropic_target["verified_features"] == [FEATURE_STREAMING]


def test_verified_target_filter_reads_one_snapshot(tmp_path, monkeypatch):
    providers = [_provider("alpha"), _provider("beta")]
    store = ConformanceStore(tmp_path / "conformance.json")
    for provider in providers:
        store.record(
            provider,
            "model-1",
            FEATURE_TOOLS,
            status=STATUS_PASS,
            classification="verified",
        )
    pool = Pool(providers, env={}, post=make_post({}), conformance=store)
    original_snapshot = store.snapshot
    calls = 0

    def counted_snapshot():
        nonlocal calls
        calls += 1
        return original_snapshot()

    monkeypatch.setattr(store, "snapshot", counted_snapshot)

    assert len(store.verified_targets(pool._all_targets(), [FEATURE_TOOLS])) == 2
    assert calls == 1


def test_proxy_forwards_response_format_and_preserves_serving_provenance(tmp_path):
    provider = _provider("alpha")
    store = ConformanceStore(tmp_path / "conformance.json")
    store.record(
        provider,
        "model-1",
        FEATURE_JSON,
        status=STATUS_PASS,
        classification="verified",
    )
    seen = {}

    def post(url, headers, body, timeout):
        seen.update(body)
        return HTTPResult(
            status=200,
            body={
                "choices": [{"message": {"role": "assistant", "content": '{"ok":true}'}}],
                "usage": {},
            },
            text="",
        )

    pool = Pool([provider], env={}, post=post, conformance=store)
    httpd = serve(pool, host="127.0.0.1", port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    payload = {
        "model": "auto",
        "messages": [{"role": "user", "content": "json"}],
        "response_format": {"type": "json_object"},
        "max_tokens": 16,
    }
    request = urllib.request.Request(
        base + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310 - local fixture
            body = json.load(response)
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert seen["response_format"] == {"type": "json_object"}
    assert body["model"] == "alpha/model-1"


def test_canary_runner_uses_only_fixed_bounded_synthetic_requests():
    provider = _provider("alpha")
    calls = []

    def call_fn(provider, model, messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        if kwargs.get("tools"):
            return Reply(
                text="",
                provider_id=provider.id,
                model=model,
                raw={},
                message={
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "record_number",
                                "arguments": '{"number":7}',
                            },
                        }
                    ]
                },
            )
        if kwargs.get("response_format"):
            return Reply(text='{"ok":true}', provider_id=provider.id, model=model, raw={})
        return Reply(text="OK", provider_id=provider.id, model=model, raw={})

    def stream_fn(provider, model, messages, **kwargs):
        calls.append({"messages": messages, "stream": True, **kwargs})
        yield "O"
        yield "K"

    results = run_target_canaries(
        provider,
        "model-1",
        env={},
        features=(
            "chat",
            "streaming",
            "tools",
            "json",
            "responses",
            "anthropic_messages",
        ),
        timeout=12,
        call_fn=call_fn,
        stream_fn=stream_fn,
    )

    assert all(row == {"status": STATUS_PASS, "classification": "verified"} for row in results.values())
    assert len(calls) == 6
    assert all(call["max_tokens"] <= 16 for call in calls)
    assert all(call["enforce_thinking_floor"] is False for call in calls if "stream" not in call)
    serialized = json.dumps(calls)
    assert "AUDIT_REPORT" not in serialized
    assert "repository" not in serialized.casefold()
    assert "user content" not in serialized.casefold()


def test_canary_runner_classifies_unsupported_without_serializing_error_text():
    provider = _provider("alpha")

    def call_fn(*args, **kwargs):
        raise ProviderHTTPError(
            400,
            "tools unsupported SECRET_SHOULD_NOT_LEAK",
            retryable=True,
        )

    results = run_target_canaries(
        provider,
        "model-1",
        env={},
        features=(FEATURE_TOOLS,),
        call_fn=call_fn,
    )

    assert results == {
        FEATURE_TOOLS: {
            "status": STATUS_UNSUPPORTED,
            "classification": "unsupported",
        }
    }
    assert "SECRET_SHOULD_NOT_LEAK" not in json.dumps(results)


def test_canary_runner_rejects_unknown_or_excessive_feature_matrix():
    provider = _provider("alpha")
    with pytest.raises(ValueError, match="unknown"):
        run_target_canaries(provider, "model-1", env={}, features=("bogus",))
    with pytest.raises(ValueError, match="at most"):
        run_target_canaries(
            provider,
            "model-1",
            env={},
            features=("chat",) * 20,
        )
