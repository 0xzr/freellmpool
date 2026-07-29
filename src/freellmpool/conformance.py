"""Per-model protocol conformance evidence and deterministic canary validators.

The store deliberately contains only bounded classifications and timestamps. Provider
responses, prompts, exception strings, credentials, and user/repository content are
never persisted.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import ProviderHTTPError
from .models import Provider, Reply

if TYPE_CHECKING:
    from .router import Target

try:  # pragma: no cover - Windows fallback
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - POSIX fallback
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None  # type: ignore[assignment]


FEATURE_CHAT = "chat"
FEATURE_STREAMING = "streaming"
FEATURE_TOOLS = "tools"
FEATURE_JSON = "json"
FEATURE_JSON_SCHEMA = "json_schema"
FEATURE_VISION = "vision"
FEATURE_RESPONSES = "responses"
FEATURE_ANTHROPIC_MESSAGES = "anthropic_messages"
FEATURES = (
    FEATURE_CHAT,
    FEATURE_STREAMING,
    FEATURE_TOOLS,
    FEATURE_JSON,
    FEATURE_JSON_SCHEMA,
    FEATURE_VISION,
    FEATURE_RESPONSES,
    FEATURE_ANTHROPIC_MESSAGES,
)

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_UNSUPPORTED = "unsupported"
STATUS_UNAVAILABLE = "unavailable"
STATUSES = frozenset({STATUS_PASS, STATUS_FAIL, STATUS_UNSUPPORTED, STATUS_UNAVAILABLE})

_SCHEMA = 1
_MAX_BYTES = 2_000_000
_MAX_TARGETS = 1_024
_MAX_CLASSIFICATION = 64
_SAFE_CLASSIFICATION = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_MAX_CANARY_FEATURES = len(FEATURES)
_CANARY_MAX_TOKENS = 16
_OK_PROMPT = "Reply with exactly OK."
_JSON_PROMPT = 'Return exactly this JSON object: {"ok":true}'
_TOOL_PROMPT = "Call record_number exactly once with number 7. Do not answer in text."
_VISION_PROMPT = "What single color is this image? Reply with one lowercase color word."
# A fixed 1x1 red PNG. It contains no user or repository content.
_RED_PIXEL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/"
    "iZk9HQAAAABJRU5ErkJggg=="
)
_CANARY_TOOL = {
    "type": "function",
    "function": {
        "name": "record_number",
        "description": "Record the deterministic conformance-test number.",
        "parameters": {
            "type": "object",
            "properties": {"number": {"type": "integer"}},
            "required": ["number"],
            "additionalProperties": False,
        },
    },
}


def default_conformance_path(env: Mapping[str, str] | None = None) -> Path:
    """Return the local machine-readable protocol evidence path."""

    source = env if env is not None else os.environ
    override = source.get("FREELLMPOOL_CONFORMANCE_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "freellmpool" / "conformance.json"


def target_fingerprint(provider: Provider, model: str) -> str:
    """Stable identity used to invalidate evidence after adapter/model endpoint changes."""

    value = "\0".join((provider.id, provider.adapter, provider.base_url, model))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _empty_state() -> dict[str, Any]:
    return {"version": _SCHEMA, "targets": {}}


def _clean_feature(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    classification = value.get("classification")
    verified_at = value.get("verified_at")
    count = value.get("verification_count")
    if status not in STATUSES:
        return None
    if not isinstance(classification, str) or not _SAFE_CLASSIFICATION.fullmatch(classification):
        return None
    if not isinstance(verified_at, str) or len(verified_at) > 32:
        return None
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 1_000_000:
        return None
    return {
        "status": status,
        "classification": classification,
        "verified_at": verified_at,
        "verification_count": count,
    }


def _clean_state(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("version") != _SCHEMA:
        return _empty_state()
    raw_targets = value.get("targets")
    if not isinstance(raw_targets, dict) or len(raw_targets) > _MAX_TARGETS:
        return _empty_state()
    targets: dict[str, Any] = {}
    for key, raw in raw_targets.items():
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 512
            or not isinstance(raw, dict)
            or not isinstance(raw.get("fingerprint"), str)
            or len(raw["fingerprint"]) != 64
        ):
            continue
        raw_features = raw.get("features")
        if not isinstance(raw_features, dict):
            continue
        features: dict[str, Any] = {}
        for feature in FEATURES:
            cleaned = _clean_feature(raw_features.get(feature))
            if cleaned is not None:
                features[feature] = cleaned
        targets[key] = {"fingerprint": raw["fingerprint"], "features": features}
    result = {"version": _SCHEMA, "targets": targets}
    updated_at = value.get("updated_at")
    if isinstance(updated_at, str) and len(updated_at) <= 32:
        result["updated_at"] = updated_at
    return result


def _target_recency(value: object) -> str:
    """Comparable latest verification timestamp for deterministic eviction."""

    if not isinstance(value, dict) or not isinstance(value.get("features"), dict):
        return ""
    return max(
        (
            row.get("verified_at", "")
            for row in value["features"].values()
            if isinstance(row, dict) and isinstance(row.get("verified_at"), str)
        ),
        default="",
    )


class ConformanceStore:
    """Small JSON-backed feature evidence store shared by CLI, router, and proxy."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_conformance_path()
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        try:
            if self.path.stat().st_size > _MAX_BYTES:
                return _empty_state()
            with self.path.open("r", encoding="utf-8") as handle:
                return _clean_state(json.load(handle))
        except (
            FileNotFoundError,
            OSError,
            ValueError,
            json.JSONDecodeError,
            RecursionError,
        ):
            return _empty_state()

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        if fcntl is None and msvcrt is None:
            raise RuntimeError("no supported cross-process file lock is available")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        handle = open(lock_path, "a+b")
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            else:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            yield
        finally:
            if fcntl is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:
                with contextlib.suppress(OSError):
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            handle.close()

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(
            f"{self.path.suffix}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            fd = os.open(tmp, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.write("\n")
            if tmp.stat().st_size > _MAX_BYTES:
                raise OSError("conformance state exceeds bounded size")
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def record(
        self,
        provider: Provider,
        model: str,
        feature: str,
        *,
        status: str,
        classification: str,
        verified_at: str | None = None,
    ) -> None:
        """Persist one sanitized feature result, preserving only a bounded classification."""

        if feature not in FEATURES:
            raise ValueError(f"unknown conformance feature: {feature}")
        if status not in STATUSES:
            raise ValueError(f"invalid conformance status: {status}")
        if (
            not isinstance(classification, str)
            or len(classification) > _MAX_CLASSIFICATION
            or _SAFE_CLASSIFICATION.fullmatch(classification) is None
        ):
            raise ValueError("invalid conformance classification")
        stamp = verified_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if len(stamp) > 32:
            raise ValueError("invalid conformance timestamp")
        key = f"{provider.id}/{model}"
        with self._lock, self._file_lock():
            state = self._load()
            targets = state["targets"]
            raw = targets.get(key)
            fingerprint = target_fingerprint(provider, model)
            if not isinstance(raw, dict) or raw.get("fingerprint") != fingerprint:
                if key not in targets and len(targets) >= _MAX_TARGETS:
                    oldest = min(
                        targets,
                        key=lambda target: (_target_recency(targets[target]), target),
                    )
                    del targets[oldest]
                raw = {"fingerprint": fingerprint, "features": {}}
                targets[key] = raw
            previous = raw["features"].get(feature)
            count = int(previous.get("verification_count", 0)) + 1 if previous else 1
            raw["features"][feature] = {
                "status": status,
                "classification": classification,
                "verified_at": stamp,
                "verification_count": min(count, 1_000_000),
            }
            state["updated_at"] = stamp
            self._save(state)

    def snapshot(self) -> dict[str, Any]:
        """Return a sanitized current snapshot without mutating a malformed source file."""

        with self._lock:
            return self._load()

    def evidence(
        self,
        provider: Provider,
        model: str,
        *,
        snapshot: Mapping[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Feature evidence for the exact current target identity, or an empty mapping."""

        state = self.snapshot() if snapshot is None else snapshot
        raw_targets = state.get("targets")
        if not isinstance(raw_targets, Mapping):
            return {}
        raw = raw_targets.get(f"{provider.id}/{model}")
        if not isinstance(raw, dict) or raw.get("fingerprint") != target_fingerprint(provider, model):
            return {}
        return {name: dict(row) for name, row in raw.get("features", {}).items()}

    def passes(
        self,
        provider: Provider,
        model: str,
        features: Iterable[str],
        *,
        snapshot: Mapping[str, Any] | None = None,
    ) -> bool:
        evidence = self.evidence(provider, model, snapshot=snapshot)
        return all(evidence.get(feature, {}).get("status") == STATUS_PASS for feature in features)

    def verified_targets(
        self,
        targets: Iterable[Target],
        features: Iterable[str],
    ) -> list[Target]:
        wanted = frozenset(features)
        if not wanted:
            return list(targets)
        snapshot = self.snapshot()
        return [
            target
            for target in targets
            if self.passes(target.provider, target.model, wanted, snapshot=snapshot)
        ]


def _has_vision_content(messages: Iterable[object] | None) -> bool:
    for message in messages or ():
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind in {"image", "image_url", "input_image"} or "image_url" in part:
                return True
    return False


def required_features(
    messages: Iterable[object] | None,
    *,
    tools: list[Any] | None = None,
    response_format: object | None = None,
    stream: bool = False,
    protocol: str | None = None,
) -> frozenset[str]:
    """Infer protocol features required by a request without inspecting its text."""

    features: set[str] = set()
    if tools:
        features.add(FEATURE_TOOLS)
    if isinstance(response_format, Mapping):
        response_type = response_format.get("type")
        if response_type == "json_object":
            features.add(FEATURE_JSON)
        elif response_type == "json_schema":
            features.add(FEATURE_JSON_SCHEMA)
    if stream:
        features.add(FEATURE_STREAMING)
    if _has_vision_content(messages):
        features.add(FEATURE_VISION)
    if protocol == FEATURE_RESPONSES:
        features.add(FEATURE_RESPONSES)
    elif protocol == FEATURE_ANTHROPIC_MESSAGES:
        features.add(FEATURE_ANTHROPIC_MESSAGES)
    return frozenset(features)


def classify_canary_exception(exc: Exception) -> str:
    """Return a privacy-safe failure class; never include the exception message."""

    if isinstance(exc, ProviderHTTPError):
        if exc.status in {400, 404, 405, 415, 422}:
            return "unsupported"
        if exc.status == 429:
            return "rate_limit"
        if exc.status in {401, 403}:
            return "auth"
        if exc.status == 402:
            return "quota"
        if exc.status == 408:
            return "timeout"
        if exc.status >= 500:
            return "availability"
        return "client"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "transport"


def _normalized_ok(text: str) -> bool:
    return text.strip().rstrip(".").strip().casefold() == "ok"


def validate_canary_result(feature: str, result: Reply | str | Iterable[str]) -> str:
    """Validate normalized feature semantics, not merely syntactic provider success."""

    if feature == FEATURE_STREAMING:
        if isinstance(result, str):
            text = result
        elif isinstance(result, Reply):
            text = result.text
        else:
            text = "".join(part for part in result if isinstance(part, str))
        return "verified" if _normalized_ok(text) else "semantic_mismatch"
    if not isinstance(result, Reply):
        return "malformed_result"
    if feature in {FEATURE_CHAT, FEATURE_RESPONSES, FEATURE_ANTHROPIC_MESSAGES}:
        return "verified" if _normalized_ok(result.text) else "semantic_mismatch"
    if feature in {FEATURE_JSON, FEATURE_JSON_SCHEMA}:
        try:
            value = json.loads(result.text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return "malformed_json"
        return "verified" if value == {"ok": True} else "semantic_mismatch"
    if feature == FEATURE_TOOLS:
        calls = result.message.get("tool_calls") if isinstance(result.message, dict) else None
        if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
            return "malformed_tool_call"
        function = calls[0].get("function")
        if not isinstance(function, dict) or function.get("name") != "record_number":
            return "semantic_mismatch"
        try:
            arguments = json.loads(function.get("arguments", ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return "malformed_tool_call"
        return "verified" if arguments == {"number": 7} else "semantic_mismatch"
    if feature == FEATURE_VISION:
        return "verified" if result.text.strip().casefold() == "red" else "semantic_mismatch"
    raise ValueError(f"unknown conformance feature: {feature}")


def _canary_status(classification: str) -> str:
    if classification == "verified":
        return STATUS_PASS
    if classification == "unsupported":
        return STATUS_UNSUPPORTED
    if classification in {"rate_limit", "quota", "auth", "timeout", "availability", "transport"}:
        return STATUS_UNAVAILABLE
    return STATUS_FAIL


def run_target_canaries(
    provider: Provider,
    model: str,
    *,
    env: dict[str, str],
    features: Iterable[str] = FEATURES,
    timeout: float = 20.0,
    call_fn: Callable[..., Reply] | None = None,
    stream_fn: Callable[..., Iterable[str]] | None = None,
) -> dict[str, dict[str, str]]:
    """Run a deterministic, quota-bounded feature matrix for one exact target.

    Each selected feature performs at most one provider call with at most 16 output
    tokens. Inputs are module constants only. Returned rows contain no response,
    exception, credential, prompt, repository, or user content.
    """

    selected = tuple(features)
    if len(selected) > _MAX_CANARY_FEATURES:
        raise ValueError(f"canary matrix supports at most {_MAX_CANARY_FEATURES} features")
    unknown = sorted(set(selected) - set(FEATURES))
    if unknown:
        raise ValueError(f"unknown conformance feature(s): {', '.join(unknown)}")
    if len(set(selected)) != len(selected):
        raise ValueError("canary matrix features must be unique")
    bounded_timeout = max(0.1, min(float(timeout), 60.0))
    if call_fn is None or stream_fn is None:
        from . import client

        call_fn = call_fn or client.call
        stream_fn = stream_fn or client.stream_call
    api_key = provider.api_key(env)
    results: dict[str, dict[str, str]] = {}
    for feature in selected:
        try:
            if feature == FEATURE_STREAMING:
                chunks = list(
                    stream_fn(
                        provider,
                        model,
                        [{"role": "user", "content": _OK_PROMPT}],
                        api_key=api_key,
                        env=env,
                        max_tokens=_CANARY_MAX_TOKENS,
                        temperature=0.0,
                        timeout=bounded_timeout,
                    )
                )
                classification = validate_canary_result(feature, chunks)
            else:
                messages: list[dict[str, Any]]
                if feature in {FEATURE_JSON, FEATURE_JSON_SCHEMA}:
                    messages = [{"role": "user", "content": _JSON_PROMPT}]
                elif feature == FEATURE_TOOLS:
                    messages = [{"role": "user", "content": _TOOL_PROMPT}]
                elif feature == FEATURE_VISION:
                    messages = [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": _VISION_PROMPT},
                                {"type": "image_url", "image_url": {"url": _RED_PIXEL}},
                            ],
                        }
                    ]
                elif feature == FEATURE_RESPONSES:
                    from .proxy import _responses_input_to_messages, _to_responses_object

                    messages = _responses_input_to_messages({"input": _OK_PROMPT})
                elif feature == FEATURE_ANTHROPIC_MESSAGES:
                    from .anthropic_shim import reply_to_message, request_to_chat

                    translated = request_to_chat(
                        {
                            "model": model,
                            "max_tokens": _CANARY_MAX_TOKENS,
                            "messages": [{"role": "user", "content": _OK_PROMPT}],
                        }
                    )
                    messages = translated["messages"]
                else:
                    messages = [{"role": "user", "content": _OK_PROMPT}]
                call_kwargs: dict[str, Any] = {}
                if feature == FEATURE_JSON:
                    call_kwargs["response_format"] = {"type": "json_object"}
                elif feature == FEATURE_JSON_SCHEMA:
                    call_kwargs["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "freellmpool_conformance",
                            "strict": True,
                            "schema": {
                                "type": "object",
                                "properties": {"ok": {"type": "boolean"}},
                                "required": ["ok"],
                                "additionalProperties": False,
                            },
                        },
                    }
                elif feature == FEATURE_TOOLS:
                    call_kwargs["tools"] = [_CANARY_TOOL]
                    call_kwargs["tool_choice"] = {
                        "type": "function",
                        "function": {"name": "record_number"},
                    }
                reply = call_fn(
                    provider,
                    model,
                    messages,
                    api_key=api_key,
                    env=env,
                    max_tokens=_CANARY_MAX_TOKENS,
                    temperature=0.0,
                    timeout=bounded_timeout,
                    enforce_thinking_floor=False,
                    **call_kwargs,
                )
                if feature == FEATURE_RESPONSES:
                    framed = _to_responses_object(reply)
                    content = framed.get("output", [{}])[0].get("content", [{}])[0].get("text", "")
                    reply = Reply(
                        text=content,
                        provider_id=reply.provider_id,
                        model=reply.model,
                        raw={},
                    )
                elif feature == FEATURE_ANTHROPIC_MESSAGES:
                    framed = reply_to_message(reply, model)
                    content = framed.get("content", [{}])[0].get("text", "")
                    reply = Reply(
                        text=content,
                        provider_id=reply.provider_id,
                        model=reply.model,
                        raw={},
                    )
                classification = validate_canary_result(feature, reply)
        except Exception as exc:  # noqa: BLE001 - every feature result remains advisory
            classification = classify_canary_exception(exc)
        results[feature] = {
            "status": _canary_status(classification),
            "classification": classification,
        }
    return results
