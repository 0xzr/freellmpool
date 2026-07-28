"""Hot-path hardening: retryable client errors, embeddings accounting, stream
truncation signalling, and the non-stream response cap + wall-clock deadline."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest
from helpers import make_post, make_stream_post, openai_body

from freellmpool import client as C
from freellmpool.errors import AllProvidersExhausted, ProviderHTTPError
from freellmpool.models import Model, Provider
from freellmpool.proxy import serve
from freellmpool.router import Pool


def _serve(pool):
    httpd = serve(pool, host="127.0.0.1", port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


# ---- #6: honor ProviderHTTPError.retryable ----


def test_nonretryable_client_error_is_surfaced(providers, env, quota):
    post = make_post({"test": (400, {"error": {"message": "bad request"}})})  # all *.test 400
    pool = Pool(providers, quota=quota, env=env, post=post)
    with pytest.raises(AllProvidersExhausted) as ei:
        pool.chat([{"role": "user", "content": "hi"}], providers=["alpha", "beta"])
    assert ei.value.client_status == 400


def test_retryable_5xx_sets_no_client_status(providers, env, quota):
    post = make_post({"test": (503, {"error": "down"})})
    pool = Pool(providers, quota=quota, env=env, post=post)
    with pytest.raises(AllProvidersExhausted) as ei:
        pool.chat([{"role": "user", "content": "hi"}], providers=["alpha", "beta"])
    assert ei.value.client_status is None  # 5xx is retryable, not a client error


def test_provider_account_quota_is_not_surfaced_as_client_error(providers, env, quota):
    post = make_post(
        {"test": (402, {"error": "You have depleted your monthly included credits."})}
    )
    pool = Pool(providers[:2], quota=quota, env=env, post=post)

    with pytest.raises(AllProvidersExhausted) as ei:
        pool.chat([{"role": "user", "content": "hi"}])

    assert ei.value.client_status is None


def test_proxy_surfaces_client_error_status(providers, env, quota):
    post = make_post({"test": (400, {"error": {"message": "bad request"}})})
    pool = Pool(providers, quota=quota, env=env, post=post, stream_post=make_stream_post({}))
    httpd, base = _serve(pool)
    try:
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(
                {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
            ).encode(),
            headers={"content-type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req)  # noqa: S310
        assert ei.value.code == 400  # the real client error, not a generic 502
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---- #4: embeddings recorded in quota + stats ----


def test_embed_records_quota_and_stats(quota):
    emb = Provider(
        id="cohere",
        label="cohere",
        adapter="openai",
        base_url="https://cohere.test/v1",
        key_env="COHERE_API_KEY",
        models=(Model("emb-1"),),
    )
    post = make_post(
        {
            "cohere.test": (
                200,
                {"data": [{"embedding": [0.1, 0.2, 0.3]}], "usage": {"prompt_tokens": 4}},
            )
        }
    )
    pool = Pool([], quota=quota, env={"COHERE_API_KEY": "x"}, post=post, embedders=[emb])
    pool.embed("hello")
    assert quota.used("cohere", "emb-1") == 1
    assert pool.stats["requests"] == 1
    assert pool.stats["prompt_tokens"] == 4


# ---- #2: mid-stream failure signals an error, not a fake clean stop ----


def test_stream_midstream_failure_signals_error(providers, env, quota):
    def raising_stream_post(url, headers, body, timeout):
        def gen():
            yield "data: " + json.dumps({"choices": [{"delta": {"content": "par"}}]})
            raise RuntimeError("upstream died mid-stream")

        return 200, gen()

    pool = Pool(
        providers, quota=quota, env=env, post=make_post({}), stream_post=raising_stream_post
    )
    httpd, base = _serve(pool)
    try:
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(
                {"model": "auto", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
            ).encode(),
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            body = resp.read().decode()
        assert "stream_truncated" in body  # explicit truncation signal
        assert '"error"' in body
        assert '"finish_reason": "stop"' not in body  # must NOT look like a clean completion
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---- #3 / #7: non-stream response byte cap + wall-clock deadline ----


class _FakeCM:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self._resp

    def __exit__(self, *a):
        return False


class _FakeResp:
    status_code = 200

    def __init__(self, chunks):
        self._chunks = chunks

    def iter_bytes(self):
        yield from self._chunks


def test_default_post_caps_oversized_response(monkeypatch):
    big = [b"x" * (8 * 1024 * 1024)] * 6  # 48 MiB > 32 MiB cap

    class Client:
        def stream(self, *a, **k):
            return _FakeCM(_FakeResp(big))

    monkeypatch.setattr(C, "_client", lambda: Client())
    with pytest.raises(ProviderHTTPError):
        C.default_post("https://x.test/v1", {}, {}, 30.0)


def test_default_post_enforces_deadline(monkeypatch):
    monkeypatch.setattr(
        C,
        "_client",
        lambda: type(
            "Cl", (), {"stream": lambda self, *a, **k: _FakeCM(_FakeResp([b"a", b"b"]))}
        )(),
    )
    times = iter(
        [1000.0, 1000.5, 1000.5, 9999.0]
    )  # deadline calc, attempt check, chunk1 ok, chunk2 past deadline
    monkeypatch.setattr(C.time, "monotonic", lambda: next(times))
    with pytest.raises(ProviderHTTPError):
        C.default_post("https://x.test/v1", {}, {}, 30.0)


class _FakeStreamResp:
    def __init__(self, chunks):
        self._chunks = chunks

    def iter_text(self):
        yield from self._chunks


class _NoopCM:
    def __exit__(self, *a):
        return False


def test_streamlines_splits_lines_and_releases():
    closed = []
    cm = _NoopCM()
    cm.__exit__ = lambda *a: (closed.append(True), False)[1]  # noqa: E731
    sl = C._StreamLines(cm, _FakeStreamResp(["data: a\r\ndata: ", "b\n"]), deadline=None)
    assert list(sl) == ["data: a", "data: b"]  # CRLF stripped, split across chunks
    assert closed  # connection released on exhaustion


def test_streamlines_deadline_fires_without_a_newline():
    # the Codex blocker: a slow-drip upstream that never sends a newline must still
    # hit the deadline (checked per chunk, not only between completed lines).
    sl = C._StreamLines(_NoopCM(), _FakeStreamResp(["partial-no-newline-ever"]), deadline=0.0)
    with pytest.raises(ProviderHTTPError):
        list(sl)


def test_default_post_normal_response_ok(monkeypatch):
    raw = json.dumps(openai_body("hi")).encode()

    class Client:
        def stream(self, *a, **k):
            return _FakeCM(_FakeResp([raw]))

    monkeypatch.setattr(C, "_client", lambda: Client())
    res = C.default_post("https://x.test/v1", {}, {}, 30.0)
    assert res.status == 200
    assert res.body["choices"][0]["message"]["content"] == "hi"
