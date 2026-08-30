"""Persistent, privacy-safe per-route health and circuit breakers."""

from __future__ import annotations

import atexit
import json
import math
import os
import tempfile
import threading
import time
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised on POSIX CI; fallback keeps Windows usable
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

try:  # pragma: no cover - imported only on Windows
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None

_VERSION = 1
_VALID_STATES = {"closed", "open", "half_open"}
_VALID_FAILURE_CLASSES = {
    "auth",
    "availability",
    "capability",
    "client",
    "empty",
    "provider_quota",
    "rate_limit",
    "retirement",
    "timeout",
    "transport",
    "other",
}
_MAX_SAMPLES = 1_000
_MAX_INTEGER = 2**63 - 1
_MAX_STATE_BYTES = 2_000_000
_UNKNOWN_SCORE = 0.5
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_LIVE_STORES: weakref.WeakSet[RouteHealthStore] = weakref.WeakSet()


def _reset_path_locks_after_fork() -> None:
    """Replace process-local locks whose owners may not exist in the child."""
    global _PATH_LOCKS, _PATH_LOCKS_GUARD
    _PATH_LOCKS = {}
    _PATH_LOCKS_GUARD = threading.Lock()
    for store in tuple(_LIVE_STORES):
        store._after_fork_child()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_path_locks_after_fork)


def default_route_health_path(env: dict[str, str] | None = None) -> Path:
    env = env if env is not None else dict(os.environ)
    override = env.get("FREELLMPOOL_HEALTH_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "freellmpool" / "route_health.json"


@dataclass(frozen=True)
class HealthRecord:
    """Sanitized rolling health for one ``provider/model`` or ``provider/*`` key."""

    state: str = "closed"
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    ewma_ms: float | None = None
    last_success: float | None = None
    last_failure: float | None = None
    failure_class: str | None = None
    open_until: float = 0.0
    half_open_until: float = 0.0
    lease_generation: int = 0
    open_count: int = 0
    updated_at: float = 0.0

    @property
    def total(self) -> int:
        return self.successes + self.failures

    @property
    def success_rate(self) -> float:
        return 1.0 if self.total == 0 else self.successes / self.total


@dataclass(frozen=True)
class HealthLease:
    """Attempt ownership used to reject out-of-order circuit transitions."""

    started_at: float
    generations: dict[str, int]


@dataclass(frozen=True)
class FailureUpdate:
    key: str
    failure_class: str
    retry_after: float | None = None
    counts_for_health: bool = True
    open_immediately: bool = False


@dataclass(frozen=True)
class _SuccessUpdate:
    keys: tuple[str, ...]
    latency_ms: float
    lease: HealthLease | None
    recorded_at: float


class RouteHealthStore:
    """Atomic, corruption-tolerant rolling health shared by CLI/proxy processes.

    Only route identifiers, timing, counters, and a normalized failure class are
    persisted. Prompt text, response content, raw errors, headers, and credentials
    are never accepted by this API.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        clock: Callable[[], float] | None = None,
        max_entries: int = 512,
        stale_after: float = 7 * 24 * 60 * 60,
        failure_threshold: int = 3,
        base_cooldown: float = 60.0,
        max_cooldown: float = 60 * 60,
        half_open_lease: float = 30.0,
        alpha: float = 0.3,
        success_flush_every: int = 1,
        success_flush_interval: float = 1.0,
    ):
        self.path = path or default_route_health_path()
        self._lock_path = self.path.with_name(f"{self.path.name}.lock")
        self._clock = clock or time.time
        self.max_entries = max(1, int(max_entries))
        self.stale_after = max(0.0, float(stale_after))
        self.failure_threshold = max(1, int(failure_threshold))
        self.base_cooldown = max(0.0, float(base_cooldown))
        self.max_cooldown = max(self.base_cooldown, float(max_cooldown))
        self.half_open_lease = max(1.0, float(half_open_lease))
        self.alpha = max(0.0, min(1.0, float(alpha)))
        self.success_flush_every = max(1, int(success_flush_every))
        self.success_flush_interval = max(0.001, float(success_flush_interval))
        self._thread_lock = threading.RLock()
        self._fallback: dict[str, dict[str, Any]] = {}
        self._pending_successes: list[_SuccessUpdate] = []
        self._success_flush_timer: threading.Timer | None = None
        self._future_top_level: dict[str, Any] = {}
        self._quarantine_on_write = False
        _LIVE_STORES.add(self)
        if self.success_flush_every > 1:
            atexit.register(self.flush)

    def _after_fork_child(self) -> None:
        """Drop parent-owned success samples and reset child-local locks."""
        self._thread_lock = threading.RLock()
        self._pending_successes = []
        self._success_flush_timer = None

    def snapshot(self) -> dict[str, HealthRecord]:
        """Return fresh non-stale records; unreadable/corrupt state behaves empty."""
        now = self._clock()
        with self._thread_lock:
            try:
                with self._file_lock():
                    routes = self._load()
                clean = self._bound(self._clean(routes, now))
                self._fallback = clean
                visible = {key: dict(row) for key, row in clean.items()}
                self._apply_pending_successes(visible)
                clean = self._bound(self._clean(visible, now))
            except OSError:
                visible = {
                    key: dict(row) for key, row in self._fallback.items()
                }
                self._apply_pending_successes(visible)
                clean = self._bound(self._clean(visible, now))
            return {key: _record(row) for key, row in clean.items()}

    def state(self, key: str) -> HealthRecord | None:
        return self.snapshot().get(key)

    def sample_age(self, row: HealthRecord | None) -> float | None:
        if row is None:
            return None
        return max(0.0, self._clock() - row.updated_at)

    def reset_remaining(self, row: HealthRecord | None) -> float:
        if row is None:
            return 0.0
        now = self._clock()
        if row.state == "half_open":
            return max(0.0, row.half_open_until - now)
        if row.state == "open":
            return max(0.0, row.open_until - now)
        return 0.0

    def provider_cooldowns(self) -> dict[str, float]:
        """Provider-wide persistent circuit reset times, in seconds remaining."""
        return {
            key[:-2]: self.reset_remaining(row)
            for key, row in self.snapshot().items()
            if key.endswith("/*") and row.state != "closed"
        }

    def route_cooldowns(self) -> dict[str, float]:
        """Per-model persistent circuit reset times, in seconds remaining."""
        return {
            key: self.reset_remaining(row)
            for key, row in self.snapshot().items()
            if not key.endswith("/*") and row.state != "closed"
        }

    def allow(self, key: str) -> bool:
        """Acquire permission for a request, including a single half-open lease."""
        return self.allow_many((key,))

    def allow_many(self, keys) -> bool:
        """Atomically acquire all route/provider circuit leases or none of them."""
        return self.acquire_many(keys) is not None

    def refresh_lease(self, lease: HealthLease) -> HealthLease:
        """Refresh one already-authorized attempt without acquiring an open circuit.

        Used only for a deferred transport retry from the same request. Keeping the
        persisted generations means a newer request still wins; refreshing the start
        time lets a later success recover the failure that triggered this retry.
        """
        return HealthLease(self._clock(), dict(lease.generations))

    def release_many(self, keys, *, lease: HealthLease | None) -> None:
        """Release a half-open probe after local saturation without poisoning it."""
        requested = tuple(dict.fromkeys(key for key in keys if _valid_key(key)))
        if not requested:
            return

        def mutate(
            routes: dict[str, dict[str, Any]], now: float
        ) -> tuple[None, bool]:
            dirty = False
            for key in requested:
                row = routes.get(key)
                if (
                    row is not None
                    and _state(row.get("state")) == "half_open"
                    and _owns_transition(row, key, lease)
                ):
                    # No upstream attempt occurred, so neither heal nor poison
                    # the circuit. Expire the probe lease and let the next request
                    # acquire a fresh single half-open generation immediately.
                    row.update(
                        {
                            "state": "open",
                            "open_until": now,
                            "half_open_until": 0.0,
                            "updated_at": now,
                        }
                    )
                    dirty = True
            return None, dirty

        self._update(mutate)

    def acquire_many(self, keys) -> HealthLease | None:
        """Acquire circuits and return ownership for conditional result updates."""
        requested = tuple(dict.fromkeys(key for key in keys if _valid_key(key)))
        if not requested:
            return HealthLease(started_at=self._clock(), generations={})

        def mutate(
            routes: dict[str, dict[str, Any]], now: float
        ) -> tuple[HealthLease | None, bool]:
            for key in requested:
                row = routes.get(key)
                if row is None:
                    continue
                state = _state(row.get("state"))
                open_until = _number(row.get("open_until")) or 0.0
                half_open_until = _number(row.get("half_open_until")) or 0.0
                if state == "open" and open_until > now:
                    return None, False
                if state == "half_open" and half_open_until > now:
                    return None, False
            for key in requested:
                created = key not in routes
                row = routes.setdefault(key, {})
                generation = _integer(row.get("lease_generation"))
                row["lease_generation"] = (
                    1 if generation >= _MAX_INTEGER else generation + 1
                )
                if _state(row.get("state")) != "closed":
                    row["state"] = "half_open"
                    row["half_open_until"] = now + self.half_open_lease
                    row["updated_at"] = now
                elif created:
                    row["updated_at"] = now
            generations = {
                key: _integer(routes.get(key, {}).get("lease_generation"))
                for key in requested
            }
            return HealthLease(now, generations), True

        return self._update(mutate)

    def record_success(
        self,
        key: str,
        latency_ms: float,
        *,
        lease: HealthLease | None = None,
    ) -> None:
        self.record_success_many((key,), latency_ms, lease=lease)

    def record_success_many(
        self,
        keys,
        latency_ms: float,
        *,
        lease: HealthLease | None = None,
    ) -> None:
        requested = tuple(dict.fromkeys(key for key in keys if _valid_key(key)))
        if not requested:
            return
        latency = _number(latency_ms)
        if latency is None or latency < 0:
            latency = 0.0
        event = _SuccessUpdate(requested, latency, lease, self._clock())
        with self._thread_lock:
            # A half-open success is a recovery transition and must be durable
            # immediately. acquire_many() refreshes _fallback before returning the
            # lease, so this check does not add another disk read to the hot path.
            immediate = self.success_flush_every <= 1 or any(
                _state(self._fallback.get(key, {}).get("state")) != "closed"
                for key in requested
                if key in self._fallback
            )
            if immediate:

                def mutate(
                    routes: dict[str, dict[str, Any]], now: float
                ) -> tuple[None, bool]:
                    self._apply_success(routes, event)
                    return None, True

                self._update(mutate)
                return
            self._pending_successes.append(event)
            if len(self._pending_successes) >= self.success_flush_every:
                self._flush_pending_locked()
            if self._pending_successes:
                self._schedule_success_flush_locked()

    def flush(self) -> None:
        """Persist ordinary closed-circuit success samples in one transaction."""
        with self._thread_lock:
            self._cancel_success_flush_locked()
            self._flush_pending_locked()
            if self._pending_successes:
                self._schedule_success_flush_locked()

    def _flush_pending_locked(self) -> None:
        if not self._pending_successes:
            return

        def mutate(
            routes: dict[str, dict[str, Any]], now: float
        ) -> tuple[None, bool]:
            return None, False

        self._update(mutate)

    def _schedule_success_flush_locked(self) -> None:
        if not self._pending_successes or self._success_flush_timer is not None:
            return
        timer = threading.Timer(self.success_flush_interval, self.flush)
        timer.daemon = True
        self._success_flush_timer = timer
        timer.start()

    def _cancel_success_flush_locked(self) -> None:
        timer = self._success_flush_timer
        self._success_flush_timer = None
        if timer is not None:
            timer.cancel()

    def _apply_pending_successes(self, routes: dict[str, dict[str, Any]]) -> None:
        for event in self._pending_successes:
            self._apply_success(routes, event)

    def _apply_success(
        self, routes: dict[str, dict[str, Any]], event: _SuccessUpdate
    ) -> None:
        for key in event.keys:
            row = routes.setdefault(key, {})
            _roll_counts(row)
            row["successes"] = _integer(row.get("successes")) + 1
            previous = _number(row.get("ewma_ms"))
            row["ewma_ms"] = (
                event.latency_ms
                if previous is None
                else self.alpha * event.latency_ms + (1.0 - self.alpha) * previous
            )
            if _owns_transition(row, key, event.lease):
                row.update(
                    {
                        "state": "closed",
                        "consecutive_failures": 0,
                        "last_success": event.recorded_at,
                        "failure_class": None,
                        "open_until": 0.0,
                        "half_open_until": 0.0,
                        "open_count": 0,
                    }
                )
            row["updated_at"] = max(
                _number(row.get("updated_at")) or 0.0,
                event.recorded_at,
            )

    def record_failure(
        self,
        key: str,
        failure_class: str,
        *,
        retry_after: float | None = None,
        counts_for_health: bool = True,
        open_immediately: bool = False,
        lease: HealthLease | None = None,
    ) -> None:
        self.record_failures(
            (
                FailureUpdate(
                    key=key,
                    failure_class=failure_class,
                    retry_after=retry_after,
                    counts_for_health=counts_for_health,
                    open_immediately=open_immediately,
                ),
            ),
            lease=lease,
        )

    def record_failures(
        self,
        updates,
        *,
        lease: HealthLease | None = None,
    ) -> None:
        requested = tuple(update for update in updates if _valid_key(update.key))
        if not requested:
            return

        def mutate(routes: dict[str, dict[str, Any]], now: float) -> tuple[None, bool]:
            dirty = False
            for update in requested:
                key = update.key
                classification = (
                    update.failure_class
                    if isinstance(update.failure_class, str)
                    and update.failure_class in _VALID_FAILURE_CLASSES
                    else "other"
                )
                retry = _number(update.retry_after)
                if retry is not None:
                    retry = max(0.0, retry)
                row = routes.setdefault(key, {})
                state = _state(row.get("state"))
                owns_transition = _owns_transition(row, key, lease)
                if update.counts_for_health:
                    _roll_counts(row)
                    row["failures"] = _integer(row.get("failures")) + 1
                    if owns_transition:
                        row["consecutive_failures"] = (
                            _integer(row.get("consecutive_failures")) + 1
                        )
                else:
                    row.setdefault("successes", 0)
                    row.setdefault("failures", 0)
                    row.setdefault("consecutive_failures", 0)
                    # A non-availability response proves the endpoint answered.
                    if owns_transition and state == "half_open":
                        row.update(
                            {
                                "state": "closed",
                                "consecutive_failures": 0,
                                "open_until": 0.0,
                                "half_open_until": 0.0,
                                "open_count": 0,
                            }
                        )
                should_open = (
                    owns_transition
                    and update.counts_for_health
                    and (
                        update.open_immediately
                        or state == "half_open"
                        or _integer(row.get("consecutive_failures"))
                        >= self.failure_threshold
                    )
                )
                if should_open:
                    open_count = _integer(row.get("open_count")) + 1
                    cooldown = (
                        retry
                        if retry is not None
                        else min(
                            self.max_cooldown,
                            self.base_cooldown * (2 ** min(open_count - 1, 10)),
                        )
                    )
                    row.update(
                        {
                            "state": "open",
                            "open_until": now + cooldown,
                            "half_open_until": 0.0,
                            "open_count": open_count,
                        }
                    )
                elif owns_transition:
                    row.setdefault("state", "closed")
                    row.setdefault("open_until", 0.0)
                    row.setdefault("half_open_until", 0.0)
                    row.setdefault("open_count", 0)
                if owns_transition:
                    row["last_failure"] = now
                    row["failure_class"] = classification
                row["updated_at"] = now
                dirty = dirty or update.counts_for_health or owns_transition
            return None, dirty

        self._update(mutate)

    def routing_penalty(self, key: str) -> float:
        return score_record(self.state(key))

    def failing(self, key: str) -> bool:
        row = self.state(key)
        return bool(
            row
            and (
                row.state != "closed"
                or row.consecutive_failures >= self.failure_threshold
            )
        )

    def _update(self, mutator):
        now = self._clock()
        with self._thread_lock:
            try:
                with self._file_lock():
                    routes = self._clean(self._load(), now)
                    had_pending = bool(self._pending_successes)
                    self._apply_pending_successes(routes)
                    result, dirty = mutator(routes, now)
                    routes = self._bound(routes)
                    if dirty or had_pending:
                        self._write(routes)
                        self._pending_successes.clear()
                        self._cancel_success_flush_locked()
                self._fallback = routes
                return result
            except (OSError, OverflowError, ValueError):
                # Keep fallback as the best-known durable/base state. Pending
                # successes remain a separate overlay after a failed write; folding
                # them into fallback here would count them again on every retry.
                base = self._clean(
                    {key: dict(row) for key, row in self._fallback.items()}, now
                )
                routes = {key: dict(row) for key, row in base.items()}
                self._apply_pending_successes(routes)
                result, _dirty = mutator(routes, now)
                mutator(base, now)
                self._fallback = self._bound(base)
                return result

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        path_key = str(self._lock_path.absolute())
        with _PATH_LOCKS_GUARD:
            local_lock = _PATH_LOCKS.setdefault(path_key, threading.RLock())
        with local_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self._lock_path.open("a+b") as lock_file:
                try:
                    os.chmod(self._lock_path, 0o600)
                except OSError:
                    pass
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                elif msvcrt is not None:  # pragma: no cover - Windows only
                    lock_file.seek(0, os.SEEK_END)
                    if lock_file.tell() == 0:
                        lock_file.write(b"\0")
                        lock_file.flush()
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    elif msvcrt is not None:  # pragma: no cover - Windows only
                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            with self.path.open("rb") as handle:
                raw = handle.read(_MAX_STATE_BYTES + 1)
            if len(raw) > _MAX_STATE_BYTES:
                self._quarantine_on_write = True
                return {}
            data = json.loads(raw.decode("utf-8"))
        except FileNotFoundError:
            self._future_top_level = {}
            self._quarantine_on_write = False
            return {}
        except (OSError, ValueError, UnicodeError):
            self._quarantine_on_write = self.path.exists()
            return {}
        if not isinstance(data, dict) or data.get("version") != _VERSION:
            self._quarantine_on_write = True
            return {}
        routes = data.get("routes")
        if not isinstance(routes, dict):
            self._quarantine_on_write = True
            return {}
        self._future_top_level = {
            key: value for key, value in data.items() if key not in {"version", "routes"}
        }
        self._quarantine_on_write = False
        return {
            key: dict(row)
            for key, row in routes.items()
            if _valid_key(key) and isinstance(row, dict)
        }

    def _clean(
        self, routes: dict[str, dict[str, Any]], now: float
    ) -> dict[str, dict[str, Any]]:
        if self.stale_after <= 0:
            return routes
        return {
            key: row
            for key, row in routes.items()
            if now - (_number(row.get("updated_at")) or 0.0) <= self.stale_after
        }

    def _bound(self, routes: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        if len(routes) <= self.max_entries:
            return routes
        newest = sorted(
            routes,
            key=lambda key: _number(routes[key].get("updated_at")) or 0.0,
            reverse=True,
        )[: self.max_entries]
        return {key: routes[key] for key in newest}

    def _write(self, routes: dict[str, dict[str, Any]]) -> None:
        payload = {
            **self._future_top_level,
            "version": _VERSION,
            "routes": {
                key: {**row, **asdict(_record(row))}
                for key, row in sorted(routes.items())
            },
        }
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        try:
            os.chmod(temp_name, 0o600)
            handle = os.fdopen(fd, "w", encoding="utf-8")
            fd = -1
            with handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if self._quarantine_on_write and self.path.exists():
                corrupt = self.path.with_suffix(self.path.suffix + ".corrupt")
                if corrupt.exists():
                    corrupt = self.path.with_suffix(
                        f"{self.path.suffix}.{os.getpid()}.{threading.get_ident()}.corrupt"
                    )
                os.replace(self.path, corrupt)
            os.replace(temp_name, self.path)
            self._quarantine_on_write = False
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def _valid_key(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 300
        and all(ord(char) >= 32 and char not in "\r\n\0" for char in value)
    )


def score_record(row: HealthRecord | None) -> float:
    """Routing penalty for an already-snapshotted persistent health record."""
    if row is None or row.total == 0:
        return _UNKNOWN_SCORE
    latency_s = (row.ewma_ms or 0.0) / 1000.0
    circuit = 50.0 if row.state != "closed" else 0.0
    return circuit + (1.0 - row.success_rate) * 10.0 + min(latency_s, 10.0) * 0.1


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return min(_MAX_INTEGER, max(0, result))


def _state(value: object) -> str:
    return value if isinstance(value, str) and value in _VALID_STATES else "closed"


def _record(row: dict[str, Any]) -> HealthRecord:
    failure_class = row.get("failure_class")
    if not isinstance(failure_class, str) or failure_class not in _VALID_FAILURE_CLASSES:
        failure_class = None
    return HealthRecord(
        state=_state(row.get("state")),
        successes=_integer(row.get("successes")),
        failures=_integer(row.get("failures")),
        consecutive_failures=_integer(row.get("consecutive_failures")),
        ewma_ms=_number(row.get("ewma_ms")),
        last_success=_number(row.get("last_success")),
        last_failure=_number(row.get("last_failure")),
        failure_class=failure_class,
        open_until=_number(row.get("open_until")) or 0.0,
        half_open_until=_number(row.get("half_open_until")) or 0.0,
        lease_generation=_integer(row.get("lease_generation")),
        open_count=_integer(row.get("open_count")),
        updated_at=_number(row.get("updated_at")) or 0.0,
    )


def _roll_counts(row: dict[str, Any]) -> None:
    successes = _integer(row.get("successes"))
    failures = _integer(row.get("failures"))
    if successes + failures >= _MAX_SAMPLES:
        row["successes"] = successes // 2
        row["failures"] = failures // 2


def _owns_transition(
    row: dict[str, Any],
    key: str,
    lease: HealthLease | None,
) -> bool:
    if lease is None:
        return True
    if lease.generations.get(key) != _integer(row.get("lease_generation")):
        return False
    latest = max(
        _number(row.get("last_success")) or 0.0,
        _number(row.get("last_failure")) or 0.0,
    )
    if latest > lease.started_at:
        return False
    if _state(row.get("state")) == "half_open":
        return lease.generations.get(key) == _integer(row.get("lease_generation"))
    return True
