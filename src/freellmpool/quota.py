"""Persistent, per-provider/model/day request counters.

Counters live in a JSON file (default ``~/.config/freellmpool/quota.json``) and
reset at UTC midnight. They are advisory: freellmpool uses them to spread load
and to skip providers that have hit their free-tier daily hint, but it never
guarantees a provider's real server-side limit.

The store is intentionally tiny and dependency-free so it can be embedded in
tests with an explicit path and a fixed clock.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

try:
    import fcntl  # POSIX advisory file locks
except ImportError:  # pragma: no cover - non-POSIX (Windows)
    fcntl = None


def _utc_day(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return now.astimezone(UTC).strftime("%Y-%m-%d")


def default_quota_path() -> Path:
    override = os.environ.get("FREELLMPOOL_QUOTA_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "freellmpool" / "quota.json"


class QuotaStore:
    """A small JSON-backed counter keyed by (day, provider_id, model)."""

    def __init__(
        self,
        path: Path | None = None,
        clock: Callable[[], datetime] | None = None,
        flush_every: int | None = None,
        flush_interval: float | None = None,
    ):
        self.path = path or default_quota_path()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()  # the proxy is threaded; guard read-modify-write
        if flush_every is None:
            try:
                flush_every = int(os.environ.get("FREELLMPOOL_QUOTA_FLUSH_EVERY", "1"))
            except ValueError:
                flush_every = 1
        self.flush_every = max(1, flush_every)
        if flush_interval is None:
            try:
                flush_interval = float(
                    os.environ.get("FREELLMPOOL_QUOTA_FLUSH_INTERVAL", "1.0")
                )
            except ValueError:
                flush_interval = 1.0
        self.flush_interval = max(0.001, float(flush_interval))
        self._pending_counts: dict[str, dict[str, int]] = {}
        self._pending_ops = 0
        self._flush_timer: threading.Timer | None = None
        self._data: dict = self._load()
        if self.flush_every > 1:
            atexit.register(self.flush)

    def _load(self) -> dict:
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _load_for_write(self) -> dict:
        """Load a writable object, quarantining a present corrupt file first."""
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError, ValueError):
            pass
        with contextlib.suppress(OSError):
            if self.path.exists():
                self.path.replace(self.path.with_suffix(self.path.suffix + ".corrupt"))
        return {}

    @contextlib.contextmanager
    def _file_lock(self):
        """Cross-process exclusive lock around a read-modify-write of the quota
        file, so a second process (proxy + CLI + MCP all share one file) can't
        clobber another's increments. No-op where flock is unavailable."""
        if fcntl is None:
            yield
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        try:
            fh = open(lock_path, "w")
        except OSError:
            yield  # best-effort — fall back to in-process locking only
            return
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Unique temp name so concurrent savers never clobber each other's temp.
        tmp = self.path.with_suffix(f"{self.path.suffix}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def _today(self) -> dict:
        day = _utc_day(self._clock())
        bucket = self._data.get(day)
        if bucket is None:
            # New UTC day → drop stale buckets to keep the file small.
            self._data = {day: {}}
            bucket = self._data[day]
        return bucket

    @staticmethod
    def _key(provider_id: str, model: str) -> str:
        return f"{provider_id}::{model}"

    def used(self, provider_id: str, model: str) -> int:
        with self._lock:
            return int(self._today().get(self._key(provider_id, model), 0))

    def record(self, provider_id: str, model: str, n: int = 1) -> int:
        with self._lock:
            if self.flush_every <= 1:
                return self._record_and_save_locked(provider_id, model, n)

            day = _utc_day(self._clock())
            bucket = self._today()
            key = self._key(provider_id, model)
            bucket[key] = int(bucket.get(key, 0)) + n
            self._pending_counts.setdefault(day, {})
            self._pending_counts[day][key] = int(self._pending_counts[day].get(key, 0)) + n
            self._pending_ops += 1
            count = bucket[key]
            if self._pending_ops >= self.flush_every:
                self._flush_locked()
            if self._pending_counts:
                self._schedule_flush_locked()
            return count

    def _record_and_save_locked(self, provider_id: str, model: str, n: int) -> int:
        with self._file_lock():
            # Reload under the lock so concurrent processes' increments survive
            # (we'd otherwise write a stale whole-file snapshot over theirs).
            self._data = self._load_for_write()
            bucket = self._today()
            key = self._key(provider_id, model)
            bucket[key] = int(bucket.get(key, 0)) + n
            count = bucket[key]
            try:
                self._save()
            except OSError:
                # Quota is advisory — never let a persistence hiccup abort an
                # otherwise-successful completion.
                pass
            return count

    def flush(self) -> None:
        """Persist any locally batched quota increments."""
        with self._lock:
            self._cancel_flush_timer_locked()
            self._flush_locked()
            if self._pending_counts:
                self._schedule_flush_locked()

    def _schedule_flush_locked(self) -> None:
        if not self._pending_counts or self._flush_timer is not None:
            return
        timer = threading.Timer(self.flush_interval, self.flush)
        timer.daemon = True
        self._flush_timer = timer
        timer.start()

    def _cancel_flush_timer_locked(self) -> None:
        timer = self._flush_timer
        self._flush_timer = None
        if timer is not None:
            timer.cancel()

    def _flush_locked(self) -> None:
        if not self._pending_counts:
            return
        with self._file_lock():
            merged = self._load_for_write()
            for day, changes in self._pending_counts.items():
                bucket = merged.setdefault(day, {})
                for key, amount in changes.items():
                    bucket[key] = int(bucket.get(key, 0)) + amount
            old_data = self._data
            self._data = merged
            try:
                self._save()
            except OSError:
                self._data = old_data
                return
            self._pending_counts.clear()
            self._pending_ops = 0
            self._cancel_flush_timer_locked()

    def over_budget(self, provider_id: str, model: str, rpd: int) -> bool:
        """True if a positive rpd hint exists and today's use meets/exceeds it."""
        if rpd <= 0:
            return False
        return self.used(provider_id, model) >= rpd

    def snapshot(self) -> dict[str, int]:
        """Today's counters as a flat {provider::model: count} dict.

        Reloads from disk so a long-running proxy reflects other processes, then
        overlays local pending increments in memory. Reading never forces a write."""
        with self._lock:
            current_day = _utc_day(self._clock())
            loaded = self._load()
            bucket = dict(loaded.get(current_day, {}))
            for key, amount in self._pending_counts.get(current_day, {}).items():
                bucket[key] = int(bucket.get(key, 0)) + amount
            self._data = {current_day: bucket}
            return bucket
