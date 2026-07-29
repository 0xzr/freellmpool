"""OpenAI-compatible proxy: routes, response shape, model parsing."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler

import pytest
from helpers import gemini_body, make_post, make_stream_post, openai_body

from freellmpool import __version__
from freellmpool.client import HTTPResult
from freellmpool.errors import AllProvidersExhausted
from freellmpool.proxy import (
    _MAX_BODY,
    _MAX_CONNECTIONS,
    _BoundedThreadingHTTPServer,
    _parse_model,
    make_handler,
    serve,
)
from freellmpool.router import Pool


@pytest.fixture
def server(providers, env, quota):
    post = make_post({})
    pool = Pool(providers, quota=quota, env=env, post=post, stream_post=make_stream_post({}))
    httpd = serve(pool, host="127.0.0.1", port=0)  # port 0 = ephemeral
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


def _post_json(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (localhost test)
        return resp.status, json.load(resp)


def test_chat_completions_shape(server):
    status, body = _post_json(
        server + "/v1/chat/completions",
        {"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert status == 200
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "ok"
    assert "x_freellmpool" in body


@pytest.mark.parametrize(
    ("request_model", "pool_routing"),
    [("agent", "fair"), ("auto", "agent")],
)
def test_effective_agent_route_leaves_client_deadline_margin_for_buffered_and_streaming(
    providers, env, quota, request_model, pool_routing
):
    seen: list[tuple[str, float]] = []

    def post(url, headers, body, timeout):
        seen.append(("buffered", timeout))
        return HTTPResult(200, openai_body("ok"), "ok")

    canned_stream = make_stream_post({})

    def stream_post(url, headers, body, timeout):
        seen.append(("stream", timeout))
        return canned_stream(url, headers, body, timeout)

    pool = Pool(
        providers[:2],
        quota=quota,
        env=env,
        post=post,
        stream_post=stream_post,
        routing=pool_routing,
    )
    httpd, base = _serve(pool)
    try:
        payload = {
            "model": request_model,
            "messages": [{"role": "user", "content": "hi"}],
        }
        assert _post_json(base + "/v1/chat/completions", payload)[0] == 200

        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps({**payload, "stream": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (localhost test)
            assert resp.status == 200
            resp.read()
    finally:
        httpd.shutdown()
        httpd.server_close()

    by_kind = {kind: timeout for kind, timeout in seen}
    assert by_kind["buffered"] == pytest.approx(540.0, abs=0.1)
    assert by_kind["stream"] == pytest.approx(540.0, abs=0.1)


def test_agent_route_enforces_one_overall_failover_budget(providers, env, quota):
    clock = {"now": 0.0}
    seen_timeouts: list[float] = []

    def post(url, headers, body, timeout):
        seen_timeouts.append(timeout)
        clock["now"] += 300.0
        return HTTPResult(503, {"error": "down"}, "down")

    pool = Pool(
        providers[:2],
        quota=quota,
        env=env,
        post=post,
        clock=lambda: clock["now"],
    )
    httpd, base = _serve(pool)
    try:
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "agent",
                    "messages": [{"role": "user", "content": "hi"}],
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)  # noqa: S310 (localhost test)
        assert exc_info.value.code == 502
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert seen_timeouts == [540.0, 240.0]


def test_agent_stream_fallback_shares_one_overall_budget(providers, env, quota):
    clock = {"now": 0.0}
    seen: list[tuple[str, float]] = []

    def stream_chat(*args, timeout, **kwargs):
        seen.append(("stream", timeout))
        clock["now"] += 300.0
        if False:
            yield None
        raise AllProvidersExhausted([("alpha/alpha-small", "down")])

    def post(url, headers, body, timeout):
        seen.append(("buffered", timeout))
        return HTTPResult(200, openai_body("ok"), "ok")

    pool = Pool(
        providers[:1],
        quota=quota,
        env=env,
        post=post,
        clock=lambda: clock["now"],
        routing="agent",
    )
    pool.stream_chat = stream_chat
    httpd, base = _serve(pool)
    try:
        payload = {
            "model": "auto",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (localhost test)
            assert resp.status == 200
            resp.read()
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert seen == [("stream", 540.0), ("buffered", 240.0)]


def test_proxy_server_header_uses_package_version(server):
    with urllib.request.urlopen(server + "/v1/models") as resp:  # noqa: S310
        assert f"freellmpool/{__version__}" in resp.headers["Server"]


def test_proxy_listen_backlog_matches_connection_cap():
    assert _BoundedThreadingHTTPServer.request_queue_size == _MAX_CONNECTIONS


def test_concurrent_chat_requests_share_pool_safely(providers, env, quota):
    post = make_post({})
    pool = Pool(providers, quota=quota, env=env, post=post, stream_post=make_stream_post({}))
    httpd = serve(pool, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        total = 80
        with ThreadPoolExecutor(max_workers=16) as ex:
            results = list(
                ex.map(
                    lambda _: _post_json(
                        base + "/v1/chat/completions",
                        {"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
                    ),
                    range(total),
                )
            )
        assert all(status == 200 for status, _body in results)
        assert pool.stats["requests"] == total
        assert sum(quota.snapshot().values()) == total
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_close_flushes_batched_quota(providers, env, tmp_path):
    from freellmpool.quota import QuotaStore

    quota = QuotaStore(path=tmp_path / "quota.json", flush_every=100)
    pool = Pool(providers, quota=quota, env=env, post=make_post({}))
    httpd = serve(pool, host="127.0.0.1", port=0)
    pool.ask("hi", providers=["alpha"])
    assert not (tmp_path / "quota.json").exists()
    httpd.server_close()
    assert QuotaStore(path=tmp_path / "quota.json").snapshot()


def test_models_route(server):
    with urllib.request.urlopen(server + "/v1/models") as resp:  # noqa: S310
        body = json.load(resp)
    ids = {m["id"] for m in body["data"]}
    assert "auto" in ids
    assert any(i.startswith("alpha/") for i in ids)


def test_models_route_accepts_query_string(server):
    with urllib.request.urlopen(server + "/v1/models?limit=100") as resp:  # noqa: S310
        body = json.load(resp)
    assert body["object"] == "list"
    assert any(m["id"] == "auto" for m in body["data"])


def test_anthropic_model_discovery_shape(server):
    req = urllib.request.Request(
        server + "/v1/models?limit=100",
        headers={"anthropic-version": "2023-06-01", "User-Agent": "claude-code"},
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        body = json.load(resp)
    assert body["has_more"] is False
    assert body["data"][0]["type"] == "model"
    assert body["data"][0]["id"] == "auto"
    assert body["data"][0]["display_name"] == "auto"
    ids = {m["id"] for m in body["data"]}
    assert "claude-3-5-haiku-latest" in ids


@pytest.mark.parametrize(
    "headers",
    [
        {"anthropic-version": "2023-06-01"},
        {"User-Agent": "claude-code"},
    ],
)
def test_anthropic_model_discovery_triggers_are_independent(server, headers):
    req = urllib.request.Request(server + "/v1/models?limit=100", headers=headers)
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        body = json.load(resp)
    assert body["has_more"] is False
    assert body["data"][0]["type"] == "model"


def test_openai_model_discovery_ignores_loose_claude_user_agent(server):
    req = urllib.request.Request(
        server + "/v1/models?limit=100",
        headers={"User-Agent": "my-claude-tool"},
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        body = json.load(resp)
    assert body["object"] == "list"
    assert body["data"][0]["object"] == "model"


def test_dashboard(server):
    with urllib.request.urlopen(server + "/dashboard") as resp:  # noqa: S310
        assert resp.status == 200
        assert "text/html" in resp.headers["Content-Type"]
        body = resp.read().decode()
    assert "freellmpool" in body
    assert "providers configured" in body
    assert "not spent (Claude Opus 4.8)" in body


def test_healthz(server):
    with urllib.request.urlopen(server + "/healthz") as resp:  # noqa: S310
        assert resp.status == 200
        assert json.load(resp) == {"status": "ok"}
    with urllib.request.urlopen(server + "/livez/") as resp:  # noqa: S310
        assert resp.status == 200
        assert json.load(resp) == {"status": "ok"}


def test_readyz_and_provider_inventory(server):
    with urllib.request.urlopen(server + "/readyz") as resp:  # noqa: S310
        assert resp.status == 200
        body = json.load(resp)
    assert body["schema_version"] == 1
    assert body["status"] == "ready"
    assert body["reason"] == "ready_providers_available"
    assert body["ready_providers"] == 4
    assert body["total_providers"] == 4

    with urllib.request.urlopen(server + "/v1/providers/") as resp:  # noqa: S310
        providers = json.load(resp)
    assert providers["schema_version"] == 1
    assert providers["object"] == "list"
    alpha = next(item for item in providers["data"] if item["id"] == "alpha")
    assert set(alpha) == {
        "id",
        "configured",
        "ready",
        "status",
        "enabled_models",
        "ready_models",
        "cooldown_remaining_s",
        "models",
    }
    assert set(alpha["models"][0]) == {
        "id",
        "name",
        "ready",
        "status",
        "daily_limit",
        "used_today",
        "remaining",
    }
    assert "ALPHA_KEY" not in json.dumps(providers)
    assert "https://alpha.test" not in json.dumps(providers)


@pytest.mark.parametrize("value", ["wat", "", "true&ready=false"])
def test_models_ready_query_rejects_invalid_or_repeated_values(server, value):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(server + f"/v1/models?ready={value}")  # noqa: S310
    assert exc.value.code == 400
    assert json.load(exc.value)["error"]["type"] == "invalid_request_error"


def test_models_ready_filter_preserves_content_negotiation(providers, env, quota):
    pool = Pool(providers, quota=quota, env=env, post=make_post({}))
    pool._mark_cooldown("alpha", pool._clock())
    pool._mark_cooldown("beta", pool._clock())
    pool._mark_cooldown("gee", pool._clock())
    httpd, base = _serve(pool)
    try:
        with urllib.request.urlopen(base + "/v1/models?ready=1") as resp:  # noqa: S310
            openai_body = json.load(resp)
        ids = {item["id"] for item in openai_body["data"]}
        assert "auto" in ids
        assert "free/free-1" in ids
        assert not any(item.startswith("alpha/") for item in ids)

        req = urllib.request.Request(
            base + "/v1/models/?ready=true",
            headers={"anthropic-version": "2023-06-01"},
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            anthropic_body = json.load(resp)
        assert anthropic_body["data"][0]["type"] == "model"
        anthropic_ids = {item["id"] for item in anthropic_body["data"]}
        assert anthropic_ids == ids
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_readyz_503_is_public_and_does_not_call_or_mutate(providers, quota):
    def fail_post(*_args, **_kwargs):
        raise AssertionError("readiness must not call upstream")

    pool = Pool(providers[:1], quota=quota, env={}, post=fail_post)
    before_quota = quota.snapshot()
    before_cooldown = pool.cooldown_snapshot(pool._clock())
    httpd, base = _serve(pool, api_key="secret")
    try:
        for path in ("/healthz", "/livez", "/readyz"):
            try:
                response = urllib.request.urlopen(base + path)  # noqa: S310
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                body = json.load(response)
            if path == "/readyz":
                assert response.status == 503
                assert body["status"] == "not_ready"
                assert body["reason"] == "no_ready_providers"
            else:
                assert response.status == 200

        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(base + "/v1/providers")  # noqa: S310
        assert exc.value.code == 401
        req = urllib.request.Request(
            base + "/v1/providers",
            headers={"x-api-key": "secret"},
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            assert json.load(resp)["data"][0]["status"] == "unconfigured"
        req = urllib.request.Request(
            base + "/v1/models?ready=true",
            headers={"Authorization": "Bearer secret"},
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            assert json.load(resp) == {"object": "list", "data": []}
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert quota.snapshot() == before_quota
    assert pool.cooldown_snapshot(pool._clock()) == before_cooldown


def test_status_requires_auth_while_health_stays_public(providers, env, quota):
    """Monitoring may prove reachability without exposing usage or inventory."""
    pool = Pool(providers, quota=quota, env=env, post=make_post({}))
    httpd, base = _serve(pool, api_key="secret")
    try:
        with urllib.request.urlopen(base + "/healthz") as resp:  # noqa: S310
            assert resp.status == 200
            assert json.load(resp) == {"status": "ok"}

        for path in ("/status", "/v1/status"):
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(base + path)  # noqa: S310
            assert exc.value.code == 401

            req = urllib.request.Request(
                base + path,
                headers={"Authorization": "Bearer secret"},
            )
            with urllib.request.urlopen(req) as resp:  # noqa: S310
                assert resp.status == 200
                body = json.load(resp)
                assert "providers" in body
                assert "lifetime" in body
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_tokenmax_route(server):
    status, body = _post_json(server + "/tokenmax", {"prompt": "hi", "max_models": 3})
    assert status == 200
    assert body["total"] >= 1
    assert isinstance(body["answers"], list)
    assert any(a["text"] == "ok" for a in body["answers"])  # openai-adapter fakes answer "ok"


def test_tokenmax_requires_prompt(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post_json(server + "/tokenmax", {})
    assert exc.value.code == 400


def test_status_has_tokenmax_field_idle(server):
    with urllib.request.urlopen(server + "/status") as resp:  # noqa: S310
        s = json.load(resp)
    assert s["tokenmax"]["active"] is False  # default snapshot before any run


def test_status_tokenmax_active_during_run(providers, env, quota):
    """A barrier-blocked swarm lets /status observe tokenmax.active live (the signal the
    OpenCode TUI throbs on), then settle to done==total when it finishes."""
    import time

    from helpers import openai_body

    from freellmpool.client import HTTPResult

    release = threading.Event()

    def slow_post(url, headers, json_body, timeout):
        release.wait(2.0)  # hold every fan-out call open until the test releases it
        return HTTPResult(status=200, body=openai_body("ok"), text="ok")

    pool = Pool(providers, quota=quota, env=env, post=slow_post, stream_post=make_stream_post({}))
    httpd = serve(pool, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        runner = threading.Thread(
            target=lambda: _post_json(base + "/tokenmax", {"prompt": "hi", "max_models": 3}),
            daemon=True,
        )
        runner.start()
        active_seen = False
        for _ in range(100):  # poll until the swarm is in flight
            with urllib.request.urlopen(base + "/status") as resp:  # noqa: S310
                tm = json.load(resp)["tokenmax"]
            if tm.get("active"):
                active_seen = True
                assert tm["total"] == 3
                break
            time.sleep(0.02)
        release.set()
        runner.join(timeout=3)
        assert active_seen, "tokenmax.active was never observable during the run"
        with urllib.request.urlopen(base + "/status") as resp:  # noqa: S310
            tm2 = json.load(resp)["tokenmax"]
        assert tm2["active"] is False
        assert tm2["done"] == tm2["total"] == 3
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()


def test_content_parts_flattened(server):
    status, body = _post_json(
        server + "/v1/chat/completions",
        {
            "model": "auto",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
                }
            ],
        },
    )
    assert status == 200


def test_streaming_sse(server):
    req = urllib.request.Request(
        server + "/v1/chat/completions",
        data=json.dumps(
            {"model": "auto", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        assert resp.headers["Content-Type"] == "text/event-stream"
        raw = resp.read().decode()
    assert raw.strip().endswith("[DONE]")
    chunks = [
        json.loads(ln[len("data: ") :])
        for ln in raw.splitlines()
        if ln.startswith("data: ") and "[DONE]" not in ln
    ]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert chunks[0]["choices"][0]["delta"].get("role") == "assistant"  # role delta first
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"  # stop chunk last
    content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert content == "ok"


def test_streaming_counts_tokens(providers, env, quota):
    """Streamed responses must accrue token usage (else estimated savings / tokens / tok/s never
    move for streaming clients like OpenCode). Tokens are estimated from the streamed
    text, so /status reflects the stream after it drains."""
    stream = make_stream_post({"alpha": ["Hello there, ", "this is a ", "streamed answer."]})
    pool = Pool(providers, quota=quota, env=env, post=make_post({}), stream_post=stream)
    httpd = serve(pool, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "alpha/alpha-small",  # pin the openai-adapter provider
                    "stream": True,
                    "messages": [{"role": "user", "content": "hello there"}],
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            resp.read()  # drain the whole stream so end-of-stream accounting runs
        with urllib.request.urlopen(base + "/status") as resp:  # noqa: S310
            pool_stats = json.load(resp)["pool"]
        assert pool_stats["completion_tokens"] > 0  # streamed output is now counted
        assert pool_stats["prompt_tokens"] > 0
    finally:
        httpd.shutdown()
        httpd.server_close()


def _expect_status(url, payload, headers=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def _raw_http(base: str, request: bytes, *, shutdown_write: bool = False) -> bytes:
    parts = urllib.parse.urlsplit(base)
    assert parts.hostname is not None
    assert parts.port is not None
    chunks = []
    with socket.create_connection((parts.hostname, parts.port), timeout=2.0) as sock:
        sock.settimeout(2.0)
        sock.sendall(request)
        if shutdown_write:
            sock.shutdown(socket.SHUT_WR)
        while True:
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                break
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


def test_malformed_body_returns_400_not_crash(server):
    # non-object body
    assert _expect_status(server + "/v1/chat/completions", [1, 2, 3]) == 400
    # missing messages
    assert _expect_status(server + "/v1/chat/completions", {"model": "auto"}) == 400
    # bad types
    assert (
        _expect_status(
            server + "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "hi"}], "max_tokens": "lots"},
        )
        == 400
    )
    # server still alive afterward
    assert (
        _post_json(
            server + "/v1/chat/completions",
            {"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )[0]
        == 200
    )


def test_oversized_json_body_rejected_before_reading_body(server):
    request = (
        b"POST /v1/chat/completions HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {_MAX_BODY + 1}\r\n".encode()
        + b"Connection: close\r\n\r\n"
    )
    raw = _raw_http(server, request)

    assert b" 413 " in raw.splitlines()[0]
    assert (
        _post_json(
            server + "/v1/chat/completions",
            {"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )[0]
        == 200
    )


def test_truncated_body_returns_400_and_server_stays_alive(server):
    body = b'{"model":"auto"'
    request = (
        b"POST /v1/chat/completions HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 200\r\n"
        b"Connection: close\r\n\r\n"
        + body
    )
    raw = _raw_http(server, request, shutdown_write=True)

    assert b" 400 " in raw.splitlines()[0]
    assert _expect_status(server + "/v1/chat/completions", {"model": "auto"}) == 400


def test_slow_client_body_times_out_without_pin(providers, env, quota):
    pool = Pool(providers, quota=quota, env=env, post=make_post({}), stream_post=make_stream_post({}))
    httpd = serve(pool, host="127.0.0.1", port=0)
    httpd.RequestHandlerClass.timeout = 0.2
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        request = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 200\r\n"
            b"Connection: close\r\n\r\n"
        )
        raw = _raw_http(base, request)
        assert b" 400 " in raw.splitlines()[0]
        assert (
            _post_json(
                base + "/v1/chat/completions",
                {"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
            )[0]
            == 200
        )
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_streaming_client_disconnect_closes_upstream_iterator(providers, env, quota):
    class ClosableLines:
        def __init__(self) -> None:
            self.closed = threading.Event()

        def __iter__(self):
            try:
                for _ in range(200):
                    time.sleep(0.002)
                    yield "data: " + json.dumps(
                        {"choices": [{"delta": {"content": "x" * 2048}}]}
                    )
                yield "data: [DONE]"
            finally:
                self.closed.set()

        def close(self) -> None:
            self.closed.set()

    lines = ClosableLines()

    def stream_post(url, headers, body, timeout):
        return 200, lines

    pool = Pool(providers, quota=quota, env=env, post=make_post({}), stream_post=stream_post)
    httpd, base = _serve(pool)
    try:
        payload = json.dumps(
            {"model": "auto", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
        ).encode()
        parts = urllib.parse.urlsplit(base)
        assert parts.hostname is not None
        assert parts.port is not None
        sock = socket.create_connection((parts.hostname, parts.port), timeout=2.0)
        try:
            sock.sendall(
                b"POST /v1/chat/completions HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(payload)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + payload
            )
            raw = b""
            deadline = time.monotonic() + 2.0
            while b"data:" not in raw and time.monotonic() < deadline:
                raw += sock.recv(4096)
            assert b"data:" in raw
        finally:
            sock.close()
        assert lines.closed.wait(5.0)
        assert _get_json(base + "/status")[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_shutdown_during_inflight_request_completes_without_deadlock(providers, env, quota):
    started = threading.Event()
    release = threading.Event()

    def blocking_post(url, headers, body, timeout):
        started.set()
        assert release.wait(3.0)
        return HTTPResult(200, openai_body("ok"), "ok")

    pool = Pool(providers, quota=quota, env=env, post=blocking_post, stream_post=make_stream_post({}))
    httpd, base = _serve(pool)
    result: dict[str, object] = {}

    def call_proxy() -> None:
        result["value"] = _post_json(
            base + "/v1/chat/completions",
            {"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )

    worker = threading.Thread(target=call_proxy)
    worker.start()
    assert started.wait(2.0)
    stopper = threading.Thread(target=httpd.shutdown)
    stopper.start()
    release.set()
    worker.join(timeout=3.0)
    stopper.join(timeout=3.0)
    httpd.server_close()

    assert not worker.is_alive()
    assert not stopper.is_alive()
    assert result["value"][0] == 200


def test_proxy_auth(providers, env, quota):
    from freellmpool.proxy import serve

    pool = Pool(providers, quota=quota, env=env, post=make_post({}))
    httpd = serve(pool, host="127.0.0.1", port=0, api_key="secret")
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    body = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
    try:
        assert _expect_status(base + "/v1/chat/completions", body) == 401  # no token
        assert (
            _expect_status(base + "/v1/chat/completions", body, {"Authorization": "Bearer secret"})
            == 200
        )
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.parametrize(
    ("upstream_status", "message", "expected_status"),
    [
        (400, "bad embedding input", 400),
        (402, "You have depleted your monthly included credits", 502),
    ],
)
def test_embeddings_classify_upstream_error_status(
    env, quota, upstream_status, message, expected_status
):
    from freellmpool.models import Model, Provider

    embedder = Provider(
        id="embed",
        label="Embed",
        adapter="openai",
        base_url="https://embed.test/v1",
        auth="none",
        models=(Model("emb-1"),),
    )
    post = make_post({"embed.test": (upstream_status, {"error": {"message": message}})})
    pool = Pool([], quota=quota, env=env, post=post, embedders=[embedder])
    httpd, base = _serve(pool)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _post_json(base + "/v1/embeddings", {"model": "auto", "input": ["hello"]})
        assert exc_info.value.code == expected_status
        body = json.load(exc_info.value)
        expected_type = (
            "invalid_request_error" if expected_status == 400 else "all_providers_exhausted"
        )
        assert body["error"]["type"] == expected_type
        assert message in body["error"]["message"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_responses_shim_nonstream(server):
    status, body = _post_json(
        server + "/v1/responses",
        {"model": "auto", "instructions": "be terse", "input": "hi"},
    )
    assert status == 200
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert body["output_text"] == "ok"
    assert body["output"][0]["content"][0]["type"] == "output_text"


def test_responses_shim_input_items(server):
    status, body = _post_json(
        server + "/v1/responses",
        {
            "model": "auto",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        },
    )
    assert status == 200
    assert body["output_text"] == "ok"


def _responses_stream_events(raw):
    return [
        json.loads(line.removeprefix("data: "))
        for line in raw.splitlines()
        if line.startswith("data: ")
    ]


def _assert_responses_stream_accumulates(raw):
    """Replay the SDK's output accumulation invariants over a Responses stream."""

    events = _responses_stream_events(raw)
    assert [event["sequence_number"] for event in events] == list(range(len(events)))
    current = json.loads(json.dumps(events[0]["response"]))
    assert current["output"] == []
    for event in events[1:]:
        kind = event["type"]
        if kind == "response.output_item.added":
            current["output"].append(event["item"])
        elif kind == "response.content_part.added":
            current["output"][event["output_index"]]["content"].append(event["part"])
        elif kind == "response.output_text.delta":
            current["output"][event["output_index"]]["content"][event["content_index"]][
                "text"
            ] += event["delta"]
        elif kind == "response.output_text.done":
            current["output"][event["output_index"]]["content"][event["content_index"]][
                "text"
            ] = event["text"]
        elif kind == "response.content_part.done":
            current["output"][event["output_index"]]["content"][event["content_index"]] = (
                event["part"]
            )
        elif kind == "response.output_item.done":
            current["output"][event["output_index"]] = event["item"]
        elif kind == "response.completed":
            assert current["output"] == event["response"]["output"]
    return events


def test_responses_shim_streaming(server):
    req = urllib.request.Request(
        server + "/v1/responses",
        data=json.dumps({"model": "auto", "stream": True, "input": "hi"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        raw = resp.read().decode()
    assert "event: response.created" in raw
    assert "event: response.output_text.delta" in raw
    assert "event: response.completed" in raw
    _assert_responses_stream_accumulates(raw)


def test_responses_tools_are_forwarded_and_return_function_call_output(providers, env, quota):
    tool_calls = [
        {
            "id": "call_weather",
            "type": "function",
            "function": {"name": "weather", "arguments": '{"city":"Dublin"}'},
        }
    ]
    post = make_post(
        {
            "alpha.test": (
                200,
                {"choices": [{"message": {"content": None, "tool_calls": tool_calls}}]},
            )
        }
    )
    pool = Pool(providers[:1], quota=quota, env=env, post=post)
    httpd, base = _serve(pool)
    try:
        status, body = _post_json(
            base + "/v1/responses",
            {
                "model": "auto",
                "input": "What is the weather?",
                "tools": [
                    {
                        "type": "function",
                        "name": "weather",
                        "description": "Look up weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                        "strict": True,
                    }
                ],
                "tool_choice": {"type": "function", "name": "weather"},
            },
        )
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert status == 200
    upstream = post.calls[0]["body"]
    assert upstream["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "weather",
                "description": "Look up weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
                "strict": True,
            },
        }
    ]
    assert upstream["tool_choice"] == {
        "type": "function",
        "function": {"name": "weather"},
    }
    assert body["output_text"] == ""
    assert body["output"] == [
        {
            "type": "function_call",
            "id": "fc-call_weather",
            "call_id": "call_weather",
            "name": "weather",
            "arguments": '{"city":"Dublin"}',
            "status": "completed",
        }
    ]


def test_responses_stream_includes_function_call_events(providers, env, quota):
    tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"q":"answer"}'},
        }
    ]
    post = make_post(
        {
            "alpha.test": (
                200,
                {"choices": [{"message": {"content": None, "tool_calls": tool_calls}}]},
            )
        }
    )
    pool = Pool(providers[:1], quota=quota, env=env, post=post)
    httpd, base = _serve(pool)
    try:
        req = urllib.request.Request(
            base + "/v1/responses",
            data=json.dumps(
                {
                    "model": "auto",
                    "stream": True,
                    "input": "Use the lookup tool",
                    "tools": [
                        {
                            "type": "function",
                            "name": "lookup",
                            "parameters": {"type": "object"},
                        }
                    ],
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            raw = resp.read().decode()
    finally:
        httpd.shutdown()
        httpd.server_close()

    events = _assert_responses_stream_accumulates(raw)
    arguments_done = next(
        event
        for event in events
        if event["type"] == "response.function_call_arguments.done"
    )
    assert arguments_done["name"] == "lookup"
    assert isinstance(arguments_done["sequence_number"], int)
    completed = next(event for event in events if event["type"] == "response.completed")
    assert completed["response"]["output"][0]["type"] == "function_call"
    assert completed["response"]["output"][0]["call_id"] == "call_1"


def test_responses_function_call_history_translates_to_chat_messages():
    from freellmpool.proxy import _responses_input_to_messages

    assert _responses_input_to_messages(
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": '{"q":"answer"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "42",
                },
            ]
        }
    ) == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"q":"answer"}'},
                }
            ],
        },
        {"role": "tool", "content": "42", "tool_call_id": "call_1"},
    ]


def test_responses_rejects_non_function_tools_instead_of_silently_dropping(server):
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _post_json(
            server + "/v1/responses",
            {
                "model": "auto",
                "input": "search",
                "tools": [{"type": "web_search_preview"}],
            },
        )
    assert exc_info.value.code == 400
    assert json.load(exc_info.value)["error"]["type"] == "invalid_request_error"


def test_responses_missing_input_400(server):
    assert _expect_status(server + "/v1/responses", {"model": "auto"}) == 400


def test_proxy_alias_routes(server):
    # an OpenAI model name the pool doesn't have still routes (alias → auto)
    status, body = _post_json(
        server + "/v1/chat/completions",
        {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert status == 200
    assert body["choices"][0]["message"]["content"] == "ok"


def test_proxy_observability_headers(server):
    req = urllib.request.Request(
        server + "/v1/chat/completions",
        data=json.dumps(
            {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        assert resp.headers.get("X-Freellmpool-Provider")
        assert resp.headers.get("X-Freellmpool-Model")
        assert resp.headers.get("X-Freellmpool-Attempts")


def test_proxy_tool_calls_passthrough(providers, env, quota):
    tc = [{"id": "c", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    post = make_post(
        {
            "alpha.test": (
                200,
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": None, "tool_calls": tc}}
                    ]
                },
            )
        }
    )
    pool = Pool(providers, quota=quota, env=env, post=post)
    httpd = serve(pool, host="127.0.0.1", port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        _, body = _post_json(
            base + "/v1/chat/completions",
            {
                "model": "alpha",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {"name": "f"}}],
            },
        )
        assert body["choices"][0]["finish_reason"] == "tool_calls"
        assert body["choices"][0]["message"]["tool_calls"] == tc
    finally:
        httpd.shutdown()
        httpd.server_close()


def _serve(pool, api_key=None):
    httpd = serve(pool, host="127.0.0.1", port=0, api_key=api_key)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def test_auth_required_on_all_post_routes(providers, env, quota):
    pool = Pool(
        providers, quota=quota, env=env, post=make_post({}), stream_post=make_stream_post({})
    )
    httpd, base = _serve(pool, api_key="secret")
    routes = {
        "/v1/chat/completions": {"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        "/v1/embeddings": {"model": "auto", "input": ["x"]},
        "/v1/responses": {"model": "auto", "input": "hi"},
        "/v1/messages": {
            "model": "claude",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "hi"}],
        },
        "/v1/messages/count_tokens": {"messages": [{"role": "user", "content": "hi"}]},
    }
    try:
        for path, body in routes.items():
            assert _expect_status(base + path, body) == 401, f"{path} unauth should be 401"
            got = _expect_status(base + path, body, {"Authorization": "Bearer secret"})
            assert got != 401, f"{path} with key should not be 401 (got {got})"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_anthropic_messages_route_accepts_query_string(server):
    status, body = _post_json(
        server + "/v1/messages?beta=true",
        {
            "model": "claude-3-5-sonnet",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert status == 200
    assert body["type"] == "message"


def test_gemini_adapter_via_proxy(providers, env, quota):
    # 'gee' is a gemini-adapter provider; routing model="gee" must use the gemini body shape
    post = make_post({"gee.test": (200, gemini_body("hi from gemini"))})
    pool = Pool(providers, quota=quota, env=env, post=post)
    httpd, base = _serve(pool)
    try:
        status, body = _post_json(
            base + "/v1/chat/completions",
            {"model": "gee", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert status == 200
        assert body["choices"][0]["message"]["content"] == "hi from gemini"
        gee_call = next(c for c in post.calls if "gee.test" in c["url"])
        assert "contents" in gee_call["body"]  # gemini shape, not OpenAI
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_parse_model():
    ids = {"groq", "cerebras"}
    assert _parse_model("auto", ids) == (None, None)
    assert _parse_model("", ids) == (None, None)
    assert _parse_model("groq", ids) == (["groq"], None)
    assert _parse_model("groq/llama-3.1-8b", ids) == (["groq"], "llama-3.1-8b")
    assert _parse_model("llama-3.3-70b", ids) == (None, "llama-3.3-70b")
    # catalog model names with '/' whose prefix isn't a provider id stay whole
    assert _parse_model("openai/gpt-oss-120b", ids) == (None, "openai/gpt-oss-120b")
    assert _parse_model("qwen/qwen3-coder:free", ids) == (None, "qwen/qwen3-coder:free")


def test_get_routes_gated_by_key(providers, env, quota):
    from freellmpool.proxy import serve

    pool = Pool(providers, quota=quota, env=env, post=make_post({}))
    httpd = serve(pool, host="127.0.0.1", port=0, api_key="secret")
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        for path in ("/dashboard", "/v1/models"):
            req = urllib.request.Request(base + path)
            try:
                urllib.request.urlopen(req)  # noqa: S310
                raise AssertionError(f"{path} should require auth")
            except urllib.error.HTTPError as e:
                assert e.code == 401
        # healthz stays open
        with urllib.request.urlopen(base + "/healthz") as r:  # noqa: S310
            assert r.status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_streaming_request_with_tools_carries_tool_calls(providers, env, quota):
    # stream:true + tools uses the buffered SSE path; tool_calls must survive.
    tc = [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    post = make_post(
        {"alpha.test": (200, {"choices": [{"message": {"content": None, "tool_calls": tc}}]})}
    )
    pool = Pool(providers, quota=quota, env=env, post=post)
    httpd, base = _serve(pool)
    try:
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "alpha",
                    "stream": True,
                    "messages": [{"role": "user", "content": "hi"}],
                    "tools": [{"type": "function", "function": {"name": "f"}}],
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            raw = resp.read().decode()
        chunks = [
            json.loads(ln[len("data: ") :])
            for ln in raw.splitlines()
            if ln.startswith("data: ") and "[DONE]" not in ln
        ]
        tc_deltas = [c for c in chunks if c["choices"][0]["delta"].get("tool_calls")]
        assert tc_deltas, "no tool_calls delta emitted"
        streamed = tc_deltas[0]["choices"][0]["delta"]["tool_calls"]
        assert streamed[0]["index"] == 0  # OpenAI streaming requires per-call index
        assert streamed[0]["id"] == "c1"
        assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_messages_empty_returns_anthropic_shaped_400(server):
    code = _expect_status(server + "/v1/messages", {"model": "claude", "messages": []})
    assert code == 400


def test_messages_empty_error_envelope_is_anthropic(providers, env, quota):
    pool = Pool(providers, quota=quota, env=env, post=make_post({}))
    httpd, base = _serve(pool)
    try:
        req = urllib.request.Request(
            base + "/v1/messages",
            data=json.dumps({"model": "claude", "messages": []}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req)  # noqa: S310
            raise AssertionError("expected 400")
        except urllib.error.HTTPError as e:
            body = json.load(e)
        assert body["type"] == "error"  # Anthropic envelope, not OpenAI
        assert body["error"]["type"] == "invalid_request_error"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_null_assistant_content_not_stringified():
    # OpenAI sends content:null on assistant tool-call turns; it must not become "None"
    from freellmpool.proxy import _normalize_messages

    out = _normalize_messages([{"role": "assistant", "content": None, "tool_calls": [{"id": "x"}]}])
    assert out[0]["content"] == ""
    assert out[0]["tool_calls"] == [{"id": "x"}]


def test_multimodal_content_is_preserved_for_vision_routing():
    from freellmpool.proxy import _normalize_messages

    content = [
        {"type": "text", "text": "What color is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
    ]

    assert _normalize_messages([{"role": "user", "content": content}]) == [
        {"role": "user", "content": content}
    ]


# ---- JSON /status endpoint + per-request routing control ----


def _get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (localhost test)
        return resp.status, json.load(resp)


def test_status_endpoint_shape(server):
    status, body = _get_json(server + "/status")
    assert status == 200
    assert "routing" in body
    for k in ("requests", "prompt_tokens", "completion_tokens", "cache_hits", "usd_saved"):
        assert k in body["pool"]
    assert isinstance(body["providers"], list) and body["providers"]
    p = body["providers"][0]
    assert {"id", "configured", "cooldown_remaining_s", "models"} <= set(p)
    assert isinstance(body["recent"], list)


def test_status_v1_alias(server):
    assert _get_json(server + "/v1/status")[0] == 200


def test_status_records_served_target(server):
    _post_json(
        server + "/v1/chat/completions",
        {"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    _, body = _get_json(server + "/status")
    assert body["recent"], "expected a recent entry after a chat"
    assert {"provider", "model", "attempts"} <= set(body["recent"][0])
    assert body["pool"]["requests"] >= 1


def test_models_route_includes_routing_aliases(server):
    with urllib.request.urlopen(server + "/v1/models") as resp:  # noqa: S310
        ids = {m["id"] for m in json.load(resp)["data"]}
    assert {"auto", "agent", "fast", "quality", "fair", "spread"} <= ids


def test_spread_alias_routes(server):
    # bare + provider-qualified aliases all route and serve (incl. freellmpool/auto, which
    # must NOT be treated as a literal provider filter → 503)
    for name in (
        "agent",
        "freellmpool/agent",
        "spread",
        "freellmpool/spread",
        "freellmpool/auto",
        "auto",
    ):
        status, body = _post_json(
            server + "/v1/chat/completions",
            {"model": name, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert status == 200, name
        assert body["choices"][0]["message"]["content"] == "ok", name
        assert "x_freellmpool" in body


def test_model_name_is_treated_as_routing_keyword(server):
    # "fast" is a routing keyword, not a literal model id → served as auto + fast routing
    status, body = _post_json(
        server + "/v1/chat/completions",
        {"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert status == 200
    assert "x_freellmpool" in body


def test_header_routing_override_accepted(server):
    status, body = _post_json_with_headers(
        server + "/v1/chat/completions",
        {"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        {"X-Freellmpool-Routing": "fast"},
    )
    assert status == 200
    assert "x_freellmpool" in body


def test_task_hint_header_and_body_extension_are_validated(server):
    payload = {
        "model": "quality",
        "messages": [{"role": "user", "content": "read this"}],
    }
    status, _body = _post_json_with_headers(
        server + "/v1/chat/completions",
        payload,
        {"X-Freellmpool-Task": "grounded-reading"},
    )
    assert status == 200

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _post_json(
            server + "/v1/chat/completions",
            {**payload, "task": "not-a-model-name"},
        )
    assert exc_info.value.code == 400
    body = json.load(exc_info.value)
    assert body["error"]["type"] == "invalid_request_error"


def test_parse_multipart_form_unit():
    from freellmpool.proxy import _parse_multipart_form

    ct = "multipart/form-data; boundary=XB"
    body = (
        b'--XB\r\nContent-Disposition: form-data; name="model"\r\n\r\nm1\r\n'
        b'--XB\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
        b"Content-Type: audio/wav\r\n\r\nAUDIO\x00\x01\r\n--XB--\r\n"
    )
    f = _parse_multipart_form(ct, body)
    assert f["model"] == "m1"
    assert f["file"] == ("a.wav", b"AUDIO\x00\x01")  # binary bytes preserved


def test_parse_multipart_form_binary_safe_embedded_boundary():
    # Audio bytes that contain "--XB" (NOT preceded by CRLF) must NOT be treated as a
    # delimiter — the payload must survive intact.
    from freellmpool.proxy import _parse_multipart_form

    audio = b"PRE--XB-and-more\x00\xff"
    ct = "multipart/form-data; boundary=XB"
    body = (
        b'--XB\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n\r\n'
        + audio
        + b"\r\n--XB--\r\n"
    )
    f = _parse_multipart_form(ct, body)
    assert f["file"] == ("a.wav", audio)


def test_parse_multipart_form_filename_not_mistaken_for_name():
    # A part with ONLY filename= (no name=) must NOT be parsed as a named field — the `name`
    # inside `filename=` must not match the name= parameter.
    from freellmpool.proxy import _parse_multipart_form

    ct = "multipart/form-data; boundary=XB"
    body = (
        b'--XB\r\nContent-Disposition: form-data; filename="file"\r\n\r\nDATA\r\n'
        b'--XB\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n\r\nAUDIO\r\n'
        b"--XB--\r\n"
    )
    f = _parse_multipart_form(ct, body)
    # the real file part wins; the no-name part is skipped (not stored under "file")
    assert f["file"] == ("a.wav", b"AUDIO")


def test_parse_multipart_form_missing_closing_boundary_raises():
    from freellmpool.proxy import _parse_multipart_form

    ct = "multipart/form-data; boundary=XB"
    # no trailing "--XB--"
    body = b'--XB\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n\r\nAUDIO'
    with pytest.raises(ValueError, match="closing"):
        _parse_multipart_form(ct, body)


def _multipart_audio(boundary, audio, model="whisper-large-v3-turbo", with_file=True):
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'.encode()
    ]
    if with_file:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
            f"Content-Type: audio/wav\r\n\r\n".encode()
            + audio
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)


def _transcribe_server(providers, env, quota, text="the transcript", status=200):
    from freellmpool.client import HTTPResult
    from freellmpool.models import Model, Provider

    tr = [
        Provider(
            id="groq",
            label="Groq",
            adapter="openai",
            base_url="https://api.groq.com/openai/v1",
            key_env="GROQ_API_KEY",
            models=(Model("whisper-large-v3-turbo"),),
        )
    ]

    def fake_mp(url, headers, files, data, timeout):
        assert url.endswith("/audio/transcriptions")
        assert files["file"][0] == "a.wav"
        body = {"text": text} if status == 200 else {"error": {"message": text}}
        return HTTPResult(status=status, body=body, text=text)

    pool = Pool(
        providers,
        quota=quota,
        env={**env, "GROQ_API_KEY": "x"},
        post=make_post({}),
        stream_post=make_stream_post({}),
        transcribers=tr,
        transcribe_post=fake_mp,
    )
    httpd = serve(pool, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def test_audio_transcription_route(providers, env, quota):
    httpd, base = _transcribe_server(providers, env, quota)
    try:
        body = _multipart_audio("BOUND1", b"RIFF\x00fakeaudio")
        req = urllib.request.Request(
            base + "/v1/audio/transcriptions",
            data=body,
            headers={"Content-Type": "multipart/form-data; boundary=BOUND1"},
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            d = json.load(resp)
        assert d["text"] == "the transcript"
        assert d["x_freellmpool"]["provider"] == "groq"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.parametrize(
    ("upstream_status", "message", "expected_status"),
    [
        (400, "bad audio input", 400),
        (402, "You have depleted your monthly included credits", 502),
    ],
)
def test_audio_transcription_classifies_upstream_error_status(
    providers, env, quota, upstream_status, message, expected_status
):
    httpd, base = _transcribe_server(providers, env, quota, text=message, status=upstream_status)
    try:
        body = _multipart_audio("BOUND1", b"RIFF\x00fakeaudio")
        req = urllib.request.Request(
            base + "/v1/audio/transcriptions",
            data=body,
            headers={"Content-Type": "multipart/form-data; boundary=BOUND1"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)  # noqa: S310
        assert exc_info.value.code == expected_status
        payload = json.load(exc_info.value)
        expected_type = (
            "invalid_request_error" if expected_status == 400 else "all_providers_exhausted"
        )
        assert payload["error"]["type"] == expected_type
        assert message in payload["error"]["message"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_audio_transcription_missing_file_400(providers, env, quota):
    httpd, base = _transcribe_server(providers, env, quota)
    try:
        body = _multipart_audio("BOUND1", b"", with_file=False)
        req = urllib.request.Request(
            base + "/v1/audio/transcriptions",
            data=body,
            headers={"Content-Type": "multipart/form-data; boundary=BOUND1"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)  # noqa: S310
        assert exc.value.code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_audio_transcription_unsupported_format_400(providers, env, quota):
    # srt/vtt aren't accepted upstream — the proxy must reject them with 400, not 502.
    httpd, base = _transcribe_server(providers, env, quota)
    try:
        b = "BOUND1"
        body = (
            f'--{b}\r\nContent-Disposition: form-data; name="model"\r\n\r\nwhisper-large-v3-turbo\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="response_format"\r\n\r\nsrt\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
            f"Content-Type: audio/wav\r\n\r\n".encode()
            + b"RIFF\x00fake"
            + f"\r\n--{b}--\r\n".encode()
        )
        req = urllib.request.Request(
            base + "/v1/audio/transcriptions",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={b}"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)  # noqa: S310
        assert exc.value.code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()


def _post_json_with_headers(url, payload, headers):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (localhost test)
        return resp.status, json.load(resp)


# ---- shareable SVG badge / summary + lifetime stats ----


def test_badge_svg_route(server):
    with urllib.request.urlopen(server + "/badge.svg") as resp:  # noqa: S310
        assert resp.status == 200
        assert resp.headers.get("Content-Type", "").startswith("image/svg+xml")
        body = resp.read().decode()
    assert body.startswith("<svg")
    assert "freellmpool" in body


def test_summary_svg_route(server):
    with urllib.request.urlopen(server + "/summary.svg") as resp:  # noqa: S310
        assert resp.status == 200
        body = resp.read().decode()
    assert "<svg" in body


def test_status_has_lifetime_block(server):
    status, body = _get_json(server + "/status")
    assert status == 200
    assert "lifetime" in body
    for k in (
        "requests",
        "prompt_tokens",
        "completion_tokens",
        "cache_hits",
        "usd_saved",
        "first_seen",
    ):
        assert k in body["lifetime"]


def test_badge_requires_auth_when_keyed(providers, env, quota, monkeypatch):
    monkeypatch.delenv("FREELLMPOOL_PUBLIC_BADGE", raising=False)
    pool = Pool(
        providers, quota=quota, env=env, post=make_post({}), stream_post=make_stream_post({})
    )
    httpd, base = _serve(pool, api_key="secret")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(base + "/badge.svg")  # noqa: S310
        assert exc.value.code == 401
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_badge_public_when_opted_in(providers, env, quota, monkeypatch):
    monkeypatch.setenv("FREELLMPOOL_PUBLIC_BADGE", "1")
    pool = Pool(
        providers, quota=quota, env=env, post=make_post({}), stream_post=make_stream_post({})
    )
    httpd, base = _serve(pool, api_key="secret")
    try:
        with urllib.request.urlopen(base + "/badge.svg") as resp:  # noqa: S310
            assert resp.status == 200  # public despite the proxy key
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_max_completion_tokens_reaches_buffered_chat_and_responses(providers, env, quota):
    post = make_post({})
    pool = Pool(providers[:1], quota=quota, env=env, post=post)
    httpd, base = _serve(pool)
    try:
        status, _ = _post_json(
            base + "/v1/chat/completions",
            {
                "model": "auto",
                "max_completion_tokens": 37,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert status == 200
        status, _ = _post_json(
            base + "/v1/responses",
            {"model": "auto", "max_completion_tokens": 38, "input": "hi"},
        )
        assert status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert [call["body"]["max_tokens"] for call in post.calls] == [37, 38]


def test_max_completion_tokens_reaches_live_stream(providers, env, quota):
    stream_post = make_stream_post({})
    pool = Pool(providers[:1], quota=quota, env=env, stream_post=stream_post)
    httpd, base = _serve(pool)
    try:
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "auto",
                    "stream": True,
                    "max_completion_tokens": 39,
                    "messages": [{"role": "user", "content": "hi"}],
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            assert resp.status == 200
            resp.read()
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert stream_post.calls[0]["body"]["max_tokens"] == 39


def test_max_completion_tokens_reaches_battle_and_tokenmax(providers, env, quota):
    post = make_post({})
    pool = Pool(providers[:2], quota=quota, env=env, post=post)
    httpd, base = _serve(pool)
    try:
        status, _ = _post_json(
            base + "/freellmpool/battle",
            {"prompt": "compare", "n": 2, "max_completion_tokens": 40},
        )
        assert status == 200
        battle_calls = list(post.calls)
        post.calls.clear()
        status, _ = _post_json(
            base + "/tokenmax",
            {"prompt": "compare", "max_models": 1, "max_completion_tokens": 41},
        )
        assert status == 200
        tokenmax_calls = list(post.calls)
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert battle_calls
    assert {call["body"]["max_tokens"] for call in battle_calls} == {40}
    assert tokenmax_calls
    assert {call["body"]["max_tokens"] for call in tokenmax_calls} == {41}


@pytest.mark.parametrize("requested", [{"bad": "model"}, ["bad"], 123, None])
def test_parse_model_rejects_non_string_input(requested):
    assert _parse_model(requested, {"groq"}) == (None, None)


def test_live_stream_records_real_failover_attempts(providers, env, quota):
    stream_post = make_stream_post(
        {"alpha.test": (500, []), "beta.test": (200, ["ok"])}
    )
    pool = Pool(providers[:2], quota=quota, env=env, stream_post=stream_post)
    httpd, base = _serve(pool)
    try:
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "auto",
                    "stream": True,
                    "messages": [{"role": "user", "content": "hi"}],
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            resp.read()
        _, status = _get_json(base + "/status")
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert status["recent"][0]["provider"] == "beta"
    assert status["recent"][0]["attempts"] == len(stream_post.calls)
    assert status["recent"][0]["attempts"] > 1


def test_handler_logs_but_does_not_double_write_after_response_start(
    providers, env, quota, caplog
):
    pool = Pool(providers[:1], quota=quota, env=env, post=make_post({}))
    handler_type = make_handler(pool)
    handler = object.__new__(handler_type)
    handler._response_started = True
    errors = []

    def boom():
        handler._response_started = True
        raise RuntimeError("after headers")

    handler._do_get = boom
    handler._error = lambda *args: errors.append(args)

    with caplog.at_level("ERROR", logger="freellmpool.proxy"):
        handler.do_GET()

    assert errors == []
    assert "unexpected GET handler failure" in caplog.text


def test_header_flush_failure_is_committed_and_closes_connection(
    providers, env, quota, monkeypatch
):
    pool = Pool(providers[:1], quota=quota, env=env, post=make_post({}))
    handler_type = make_handler(pool)
    handler = object.__new__(handler_type)
    handler._response_started = False
    handler.close_connection = False
    errors = []

    def fail_during_flush(_handler):
        raise OSError("socket failed after a partial header write")

    monkeypatch.setattr(BaseHTTPRequestHandler, "end_headers", fail_during_flush)
    handler._do_get = handler.end_headers
    handler._error = lambda *args: errors.append(args)

    handler.do_GET()

    assert handler._response_started is True
    assert handler.close_connection is True
    assert errors == []


def test_handler_still_sends_redacted_500_before_response_start(providers, env, quota):
    pool = Pool(providers[:1], quota=quota, env=env, post=make_post({}))
    handler_type = make_handler(pool)
    handler = object.__new__(handler_type)
    handler._response_started = False
    errors = []

    def boom():
        raise RuntimeError("before headers")

    handler._do_post = boom
    handler._error = lambda *args: errors.append(args)
    handler.do_POST()

    assert errors == [(500, "internal error: RuntimeError", "internal_error")]


@pytest.mark.parametrize("with_tools", [False, True])
def test_chat_sse_chunks_include_integer_created_timestamp(
    providers, env, quota, with_tools
):
    post = make_post({})
    stream_post = make_stream_post({})
    pool = Pool(
        providers[:1],
        quota=quota,
        env=env,
        post=post,
        stream_post=stream_post,
    )
    httpd, base = _serve(pool)
    try:
        payload = {
            "model": "auto",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }
        if with_tools:
            payload["tools"] = [{"type": "function", "function": {"name": "f"}}]
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            raw = resp.read().decode()
    finally:
        httpd.shutdown()
        httpd.server_close()

    chunks = [
        json.loads(line.removeprefix("data: "))
        for line in raw.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    assert chunks
    assert all(isinstance(chunk["created"], int) for chunk in chunks)
    assert len({chunk["created"] for chunk in chunks}) == 1
