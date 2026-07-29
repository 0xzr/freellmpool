#!/usr/bin/env python3
"""Offline proxy stress smoke for regular user traffic.

Starts a local freellmpool proxy backed by fake in-process providers, then sends
mixed OpenAI, Responses API, and Anthropic-compatible requests through the HTTP
server. No network provider APIs are called.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

from freellmpool.client import HTTPResult  # noqa: E402
from freellmpool.models import Model, Provider  # noqa: E402
from freellmpool.proxy import serve  # noqa: E402
from freellmpool.quota import QuotaStore  # noqa: E402
from freellmpool.router import Pool  # noqa: E402

_PROFILES = {
    "ci": {"requests": 144, "concurrency": 24},
    "local": {"requests": 720, "concurrency": 64},
    "soak": {"requests": 2400, "concurrency": 128},
}


class _StressState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: Counter[str] = Counter()

    def bump(self, key: str) -> None:
        with self.lock:
            self.calls[key] += 1

    def snapshot(self) -> dict[str, int]:
        with self.lock:
            return dict(self.calls)


class _Lines:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.closed = False

    def __iter__(self):
        try:
            yield from self._lines
        finally:
            self.closed = True

    def close(self) -> None:
        self.closed = True


def _openai_body(text: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 3},
    }


def _tool_body() -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "toolu_stress",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city": "Paris"}',
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 6, "completion_tokens": 2},
    }


def _make_pool(tmp: Path, state: _StressState) -> Pool:
    providers = [
        Provider(
            id=f"p{i}",
            label=f"P{i}",
            adapter="openai",
            base_url=f"https://p{i}.test/v1",
            auth="none",
            models=(Model(f"model-{i}-a", rpd=10_000), Model(f"model-{i}-b", rpd=10_000)),
        )
        for i in range(6)
    ]
    embedders = [
        Provider(
            id="embed",
            label="Embed",
            adapter="openai",
            base_url="https://embed.test/v1",
            auth="none",
            models=(Model("embed-small", rpd=10_000),),
        )
    ]
    transcribers = [
        Provider(
            id="whisper",
            label="Whisper",
            adapter="openai",
            base_url="https://whisper.test/v1",
            auth="none",
            models=(Model("whisper-small", rpd=10_000),),
        )
    ]

    def post(url: str, headers: dict, body: dict, timeout: float) -> HTTPResult:
        del headers, timeout
        if url.endswith("/embeddings"):
            state.bump("upstream_embeddings")
            inputs = body.get("input") or []
            n = len(inputs) if isinstance(inputs, list) else 1
            return HTTPResult(
                200,
                {
                    "data": [{"embedding": [0.1, 0.2, 0.3]} for _ in range(n)],
                    "usage": {"prompt_tokens": n},
                },
                "embeddings",
            )
        state.bump("upstream_chat")
        if body.get("tools"):
            return HTTPResult(200, _tool_body(), "tool")
        return HTTPResult(200, _openai_body("ok"), "ok")

    def stream_post(url: str, headers: dict, body: dict, timeout: float):
        del url, headers, body, timeout
        state.bump("upstream_stream")
        lines = [
            'data: {"choices":[{"delta":{"content":"o"}}]}',
            'data: {"choices":[{"delta":{"content":"k"}}]}',
            "data: [DONE]",
        ]
        return 200, _Lines(lines)

    def transcribe_post(
        url: str, headers: dict, files: dict, data: dict, timeout: float
    ) -> HTTPResult:
        del url, headers, timeout
        state.bump("upstream_transcription")
        assert files["file"][1]
        text = f"transcribed {data.get('model')}"
        return HTTPResult(200, {"text": text}, text)

    return Pool(
        providers,
        quota=QuotaStore(path=tmp / "quota.json", flush_every=1000),
        env={},
        post=post,
        stream_post=stream_post,
        embedders=embedders,
        transcribers=transcribers,
        transcribe_post=transcribe_post,
        routing="spread",
    )


def _request_json(
    method: str,
    url: str,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req_headers = dict(headers or {})
    if data is not None:
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=req_headers,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - localhost only
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        if "application/json" in ctype:
            return resp.status, json.loads(raw or b"{}")
        return resp.status, raw.decode("utf-8", "replace")


def _request_sse(
    url: str,
    payload: dict,
    headers: dict[str, str] | None = None,
) -> tuple[int, list[tuple[str | None, Any]], str]:
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=req_headers,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - localhost only
        raw = resp.read().decode("utf-8", "replace")
        events: list[tuple[str | None, Any]] = []
        current_event: str | None = None
        current_data: list[str] = []
        for line in raw.splitlines():
            if not line:
                if current_data:
                    data = "\n".join(current_data)
                    if data == "[DONE]":
                        parsed: Any = data
                    else:
                        parsed = json.loads(data)
                    events.append((current_event, parsed))
                current_event = None
                current_data = []
                continue
            if line.startswith("event:"):
                current_event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                current_data.append(line[len("data:") :].strip())
        if current_data:
            data = "\n".join(current_data)
            events.append((current_event, "[DONE]" if data == "[DONE]" else json.loads(data)))
        return resp.status, events, raw


def _event_names(events: list[tuple[str | None, Any]]) -> list[str | None]:
    return [name for name, _ in events]


def _multipart(boundary: str = "BOUNDARY") -> bytes:
    return (
        f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n'
        f"whisper-small\r\n"
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n".encode()
        + b"RIFF\x00fake"
        + f"\r\n--{boundary}--\r\n".encode()
    )


def _request_multipart(url: str) -> tuple[int, Any]:
    boundary = "BOUNDARY"
    req = urllib.request.Request(
        url,
        data=_multipart(boundary),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - localhost only
        return resp.status, json.loads(resp.read() or b"{}")


def _exercise(base: str, index: int) -> tuple[str, int]:
    route = index % 12
    try:
        if route == 0:
            status, _ = _request_json("GET", f"{base}/healthz")
            return "healthz", status
        if route == 1:
            status, body = _request_json("GET", f"{base}/v1/models")
            assert body["data"]
            return "models", status
        if route == 2:
            status, body = _request_json(
                "POST",
                f"{base}/v1/chat/completions",
                {"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert body["choices"][0]["message"]["content"] == "ok"
            return "chat", status
        if route == 3:
            status, events, _ = _request_sse(
                f"{base}/v1/chat/completions",
                {
                    "model": "auto",
                    "stream": True,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            assert events[-1][1] == "[DONE]"
            text = "".join(
                (data["choices"][0].get("delta") or {}).get("content") or ""
                for _, data in events
                if isinstance(data, dict)
            )
            assert text == "ok"
            return "chat_stream", status
        if route == 4:
            status, body = _request_json(
                "POST", f"{base}/v1/embeddings", {"model": "auto", "input": ["a", "b"]}
            )
            assert len(body["data"]) == 2
            return "embeddings", status
        if route == 5:
            status, body = _request_json(
                "POST", f"{base}/v1/responses", {"model": "auto", "input": "hi"}
            )
            assert body["status"] == "completed"
            return "responses", status
        if route == 6:
            status, events, _ = _request_sse(
                f"{base}/v1/responses",
                {"model": "auto", "stream": True, "input": "hi"},
            )
            assert _event_names(events) == [
                "response.created",
                "response.output_item.added",
                "response.content_part.added",
                "response.output_text.delta",
                "response.output_text.done",
                "response.content_part.done",
                "response.output_item.done",
                "response.completed",
            ]
            assert events[0][1]["response"]["output"] == []
            assert [event["sequence_number"] for _, event in events] == list(
                range(len(events))
            )
            assert events[-1][1]["type"] == "response.completed"
            return "responses_stream", status
        if route == 7:
            status, body = _request_json(
                "POST",
                f"{base}/v1/messages",
                {
                    "model": "claude-3-5-sonnet-20241022",
                    "max_tokens": 32,
                    "system": [{"type": "text", "text": "be brief"}],
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "hi"}],
                        }
                    ],
                },
                headers={"anthropic-version": "2023-06-01"},
            )
            assert body["type"] == "message"
            return "messages", status
        if route == 8:
            status, events, _ = _request_sse(
                f"{base}/v1/messages",
                {
                    "model": "claude-3-5-haiku-20241022",
                    "stream": True,
                    "max_tokens": 32,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                headers={"anthropic-version": "2023-06-01"},
            )
            assert _event_names(events) == [
                "message_start",
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
                "message_delta",
                "message_stop",
            ]
            assert events[-1][1]["type"] == "message_stop"
            return "messages_stream", status
        if route == 9:
            status, body = _request_json(
                "POST",
                f"{base}/v1/messages",
                {
                    "model": "claude-3-5-sonnet-20241022",
                    "max_tokens": 64,
                    "tools": [
                        {
                            "name": "get_weather",
                            "description": "return weather",
                            "input_schema": {"type": "object"},
                        }
                    ],
                    "tool_choice": {"type": "any"},
                    "messages": [{"role": "user", "content": "weather in Paris?"}],
                },
                headers={"anthropic-version": "2023-06-01"},
            )
            assert body["stop_reason"] == "tool_use"
            assert body["content"][0]["type"] == "tool_use"
            assert body["content"][0]["name"] == "get_weather"
            return "messages_tool_use", status
        if route == 10:
            status, body = _request_json(
                "POST",
                f"{base}/v1/messages/count_tokens",
                {
                    "model": "claude-3-5-sonnet-20241022",
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "count these tokens"}],
                        }
                    ],
                },
                headers={"anthropic-version": "2023-06-01"},
            )
            assert body["input_tokens"] >= 1
            return "messages_count_tokens", status
        status, body = _request_multipart(f"{base}/v1/audio/transcriptions")
        assert body["text"]
        return "transcriptions", status
    except urllib.error.HTTPError as exc:
        return f"http_error_{exc.code}", exc.code
    except Exception as exc:  # noqa: BLE001 - keep a full stress summary
        return f"{type(exc).__name__}: {exc}", 0


def run_stress(*, requests: int, concurrency: int, json_output: bool = False) -> int:
    state = _StressState()
    with tempfile.TemporaryDirectory(prefix="flp-stress-") as td:
        pool = _make_pool(Path(td), state)
        httpd = serve(pool, host="127.0.0.1", port=0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        started = time.perf_counter()
        results: Counter[str] = Counter()
        failures: list[str] = []
        status_body: Any = {}
        try:
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futures = [ex.submit(_exercise, base, i) for i in range(requests)]
                for fut in as_completed(futures):
                    name, status = fut.result()
                    if status != 200:
                        failures.append(f"{name}:{status}")
                    results[name] += 1
            try:
                status_code, status_body = _request_json("GET", f"{base}/status")
                if status_code != 200:
                    failures.append(f"status:{status_code}")
            except Exception as exc:  # noqa: BLE001 - report status failures in the summary
                failures.append(f"status:{type(exc).__name__}: {exc}")
        finally:
            elapsed = time.perf_counter() - started
            httpd.shutdown()
            httpd.server_close()

    summary = {
        "ok": not failures,
        "requests": requests,
        "concurrency": concurrency,
        "elapsed_s": round(elapsed, 3),
        "routes": dict(sorted(results.items())),
        "upstream": state.snapshot(),
        "pool": status_body.get("pool", {}) if isinstance(status_body, dict) else {},
        "failures": failures[:20],
    }
    if json_output:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            "proxy stress: "
            f"{requests} requests at concurrency={concurrency} in {elapsed:.2f}s; "
            f"routes={dict(sorted(results.items()))}"
        )
        if failures:
            print("failures:", ", ".join(failures[:20]), file=sys.stderr)
    return 0 if not failures else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(_PROFILES), default="local")
    parser.add_argument("--requests", type=int)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    args = parser.parse_args(argv)

    profile = _PROFILES[args.profile]
    requests = args.requests if args.requests is not None else profile["requests"]
    concurrency = args.concurrency if args.concurrency is not None else profile["concurrency"]
    if requests <= 0 or concurrency <= 0:
        parser.error("--requests and --concurrency must be positive")
    return run_stress(requests=requests, concurrency=concurrency, json_output=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
