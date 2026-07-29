"""Adapter behavior: thinking-model handling, header shaping, stream lifecycle."""

from __future__ import annotations

import json

import pytest
from helpers import make_post, openai_body

from freellmpool import client as C
from freellmpool.errors import ProviderHTTPError
from freellmpool.models import Model, Provider

P = Provider(
    id="x",
    label="X",
    adapter="openai",
    base_url="https://x.test/v1",
    key_env="X_KEY",
    models=(Model("zai-glm-4.7"),),
)


def test_thinking_model_bumps_max_tokens():
    seen = {}

    def post(url, headers, body, timeout):
        seen.update(body)
        return C.HTTPResult(200, openai_body("ok"), "ok")

    C.call(
        P,
        "zai-glm-4.7",
        [{"role": "user", "content": "hi"}],
        api_key="k",
        env={},
        max_tokens=512,
        post=post,
    )
    assert seen["max_tokens"] >= 4096  # reasoning model got headroom


def test_thinking_model_floor_can_be_disabled_for_strictly_bounded_canary():
    seen = {}

    def post(url, headers, body, timeout):
        seen.update(body)
        return C.HTTPResult(200, openai_body("ok"), "ok")

    C.call(
        P,
        "zai-glm-4.7",
        [{"role": "user", "content": "hi"}],
        api_key="k",
        env={},
        max_tokens=8,
        enforce_thinking_floor=False,
        post=post,
    )
    assert seen["max_tokens"] == 8


def test_non_thinking_model_keeps_max_tokens():
    seen = {}

    def post(url, headers, body, timeout):
        seen.update(body)
        return C.HTTPResult(200, openai_body("ok"), "ok")

    C.call(
        P,
        "llama-3.1-8b",
        [{"role": "user", "content": "hi"}],
        api_key="k",
        env={},
        max_tokens=512,
        post=post,
    )
    assert seen["max_tokens"] == 512


def test_http_error_preserves_retry_after_for_circuit_breaker():
    def post(url, headers, body, timeout):
        return C.HTTPResult(
            429,
            {"error": {"message": "slow down"}},
            "",
            headers={"Retry-After": "75"},
        )

    with pytest.raises(ProviderHTTPError) as exc_info:
        C.call(
            P,
            "zai-glm-4.7",
            [{"role": "user", "content": "hi"}],
            api_key="k",
            env={},
            max_tokens=512,
            post=post,
        )

    assert exc_info.value.retry_after == 75.0


def test_retry_after_parser_supports_standard_and_legacy_reset_headers(monkeypatch):
    monkeypatch.setattr(C.time, "time", lambda: 1_700_000_000.0)

    assert C._retry_after_seconds({"RateLimit-Reset": "45"}) == 45.0
    assert C._retry_after_seconds({"X-RateLimit-Reset": "1700000075"}) == 75.0
    # Retry-After is authoritative when a provider returns both.
    assert (
        C._retry_after_seconds(
            {"Retry-After": "12", "X-RateLimit-Reset": "1700000075"}
        )
        == 12.0
    )


def test_stream_http_error_preserves_retry_after():
    def stream_post(url, headers, body, timeout):
        return 429, {"Retry-After": "80"}, iter(("rate limited",))

    with pytest.raises(ProviderHTTPError) as exc_info:
        list(
            C.stream_call(
                P,
                "zai-glm-4.7",
                [{"role": "user", "content": "hi"}],
                api_key="k",
                env={},
                stream_post=stream_post,
            )
        )

    assert exc_info.value.retry_after == 80.0


def test_stream_rejects_clean_truncation_without_done_marker():
    def stream_post(url, headers, body, timeout):
        return 200, iter(
            ('data: {"choices":[{"delta":{"content":"partial"}}]}',)
        )

    stream = C.stream_call(
        P,
        "zai-glm-4.7",
        [{"role": "user", "content": "hi"}],
        api_key="k",
        env={},
        stream_post=stream_post,
    )
    assert next(stream) == "partial"
    with pytest.raises(ProviderHTTPError, match="before.*DONE"):
        next(stream)


def test_tools_forwarded_and_tool_calls_preserved():
    tc = [{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    seen = {}

    def post(url, headers, body, timeout):
        seen.update(body)
        return C.HTTPResult(
            200,
            {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": tc}}]},
            "",
        )

    reply = C.call(
        P,
        "some-model",
        [{"role": "user", "content": "hi"}],
        api_key="k",
        env={},
        tools=[{"type": "function", "function": {"name": "f"}}],
        post=post,
    )
    assert "tools" in seen  # forwarded to the provider
    assert reply.message["tool_calls"] == tc  # preserved on the reply
    assert reply.text == ""


def test_think_tags_stripped():
    post = make_post({"x.test": (200, openai_body("<think>secret reasoning</think>final answer"))})
    reply = C.call(
        P, "zai-glm-4.7", [{"role": "user", "content": "hi"}], api_key="k", env={}, post=post
    )
    assert reply.text == "final answer"


# ---- streaming connection lifecycle (the real _StreamLines.close path) ----


class _SpyLines:
    """A closeable line iterator that records whether close() was called."""

    def __init__(self, lines):
        self._lines = list(lines)
        self.closed = False

    def __iter__(self):
        yield from self._lines

    def close(self):
        self.closed = True


def _sse(*deltas):
    return [f"data: {json.dumps({'choices': [{'delta': {'content': d}}]})}" for d in deltas] + [
        "data: [DONE]"
    ]


def test_stream_call_closes_on_non_200():
    spy = _SpyLines([])

    def stream_post(url, headers, body, timeout):
        return 500, spy

    gen = C.stream_call(
        P, "m", [{"role": "user", "content": "hi"}], api_key="k", env={}, stream_post=stream_post
    )
    with pytest.raises(ProviderHTTPError):
        next(gen)  # status check happens on first iteration
    assert spy.closed is True  # connection released before the error propagated


def test_stream_call_closes_on_early_break():
    spy = _SpyLines(_sse("a", "b", "c"))

    def stream_post(url, headers, body, timeout):
        return 200, spy

    gen = C.stream_call(
        P, "m", [{"role": "user", "content": "hi"}], api_key="k", env={}, stream_post=stream_post
    )
    assert next(gen) == "a"
    gen.close()  # consumer abandons the stream early
    assert spy.closed is True  # try/finally released the connection


def test_stream_call_closes_on_exhaustion():
    spy = _SpyLines(_sse("x", "y"))

    def stream_post(url, headers, body, timeout):
        return 200, spy

    out = list(
        C.stream_call(
            P,
            "m",
            [{"role": "user", "content": "hi"}],
            api_key="k",
            env={},
            stream_post=stream_post,
        )
    )
    assert out == ["x", "y"]
    assert spy.closed is True


# ---- connection pooling plumbing (no network) ----


def test_shared_client_is_singleton():
    assert C._client() is C._client()  # one pooled client reused across calls


def test_timeout_has_fast_connect():
    to = C._timeout(90.0)
    assert to.read == 90.0 and to.connect == 10.0  # fast-fail connect
    assert C._timeout(3.0).connect == 3.0  # connect never exceeds the overall timeout


class _CM:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self.resp

    def __exit__(self, *args):
        return False


class _Resp:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._raw = json.dumps(body).encode()
        self.headers = headers or {}

    def iter_bytes(self):
        yield self._raw


def test_default_post_retries_retryable_status(monkeypatch):
    calls = []

    class Client:
        def stream(self, *args, **kwargs):
            calls.append(1)
            status = 503 if len(calls) == 1 else 200
            return _CM(_Resp(status, openai_body("ok")))

    monkeypatch.setattr(C, "_client", lambda: Client())
    monkeypatch.setattr(C, "_RETRY_BACKOFF_S", 0.0)
    result = C.default_post("https://x.test/v1", {}, {}, 30.0)
    assert result.status == 200
    assert len(calls) == 2


def test_default_post_retries_transport_error(monkeypatch):
    import httpx

    calls = []

    class Client:
        def stream(self, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise httpx.ConnectError("temporary")
            return _CM(_Resp(200, openai_body("ok")))

    monkeypatch.setattr(C, "_client", lambda: Client())
    monkeypatch.setattr(C, "_RETRY_BACKOFF_S", 0.0)
    result = C.default_post("https://x.test/v1", {}, {}, 30.0)
    assert result.status == 200
    assert len(calls) == 2


def test_default_post_honors_retry_after(monkeypatch):
    calls = []
    sleeps = []

    class Client:
        def stream(self, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return _CM(_Resp(429, {"error": "slow"}, {"Retry-After": "2"}))
            return _CM(_Resp(200, openai_body("ok")))

    monkeypatch.setattr(C, "_client", lambda: Client())
    monkeypatch.setattr(C.random, "uniform", lambda lo, hi: 0.0)
    monkeypatch.setattr(C.time, "sleep", sleeps.append)
    result = C.default_post("https://x.test/v1", {}, {}, 30.0)
    assert result.status == 200
    assert sleeps == [2.0]
    assert len(calls) == 2


def test_default_post_skips_retry_after_past_timeout(monkeypatch):
    calls = []

    class Client:
        def stream(self, *args, **kwargs):
            calls.append(1)
            return _CM(_Resp(429, {"error": "slow"}, {"Retry-After": "999"}))

    monkeypatch.setattr(C, "_client", lambda: Client())
    monkeypatch.setattr(C.random, "uniform", lambda lo, hi: 0.0)
    result = C.default_post("https://x.test/v1", {}, {}, 0.5)
    assert result.status == 429
    assert len(calls) == 1


def test_default_post_returns_retryable_status_after_retry_sleep_exhausts_deadline(monkeypatch):
    calls = []
    sleeps = []

    class Client:
        def stream(self, *args, **kwargs):
            calls.append(1)
            return _CM(_Resp(429, {"error": "slow"}))

    times = iter([0.0, 0.0, 0.1, 0.1, 2.0])
    monkeypatch.setattr(C, "_client", lambda: Client())
    monkeypatch.setattr(C, "_RETRY_BACKOFF_S", 0.5)
    monkeypatch.setattr(C.random, "uniform", lambda lo, hi: 0.0)
    monkeypatch.setattr(C.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(C.time, "sleep", sleeps.append)

    result = C.default_post("https://x.test/v1", {}, {}, 1.0)
    assert result.status == 429
    assert sleeps == [0.5]
    assert len(calls) == 1


def test_default_post_does_not_retry_read_error(monkeypatch):
    import httpx

    calls = []

    class Resp:
        status_code = 200
        headers = {}

        def iter_bytes(self):
            raise httpx.ReadError("read failed")
            yield b""  # pragma: no cover

    class Client:
        def stream(self, *args, **kwargs):
            calls.append(1)
            return _CM(Resp())

    monkeypatch.setattr(C, "_client", lambda: Client())
    with pytest.raises(httpx.ReadError):
        C.default_post("https://x.test/v1", {}, {}, 30.0)
    assert len(calls) == 1


def test_client_singleton_under_concurrency():
    import threading as _t

    C._shared = None  # force re-init
    results = []

    def grab():
        results.append(C._client())

    threads = [_t.Thread(target=grab) for _ in range(16)]
    for x in threads:
        x.start()
    for x in threads:
        x.join()
    assert len({id(r) for r in results}) == 1  # all threads got the same client
