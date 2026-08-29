"""Shared TOKENMAX core — fan one prompt out to eligible ranked targets.

Used by both the MCP ``tokenmax`` tool and the ``freellmpool tokenmax`` CLI so
the fan-out logic lives in exactly one place:

* the **CLI** gets the genuine rainbow ANSI animation on a TTY (stderr), then
  prints each returned answer (and, by default, a synthesized verdict);
* the **MCP** tool emits live ``notifications/progress`` while it runs (so hosts
  like Claude Code show ``🌈 TOKENMAXXING ▸ N/total models…`` ticking up) and a
  colorful, markdown-safe rainbow banner in the result — because raw ANSI can't
  animate inside an MCP host's chat, but progress + emoji color can.

Nothing here imports the MCP or proxy layers, so it stays dependency-light.
"""

from __future__ import annotations

import concurrent.futures as _cf
import itertools
import sys
import threading
from collections.abc import Callable

from .router import Pool

# Even the default eligible-target path is bounded — a pathological catalog should
# never spawn thousands of in-flight requests. ``max_models`` can only lower this.
HARD_CAP = 256
WORKERS = 32

# A markdown-safe rainbow banner: real color that lands in EVERY MCP host without
# ANSI escape codes (which hosts strip from tool output).
RAINBOW_BANNER = "🟥🟧🟨🟩🟦🟪"

# ANSI-256 rainbow + a pulse ramp for the genuine TTY animation.
_ANSI_COLORS = (196, 208, 226, 46, 51, 21, 201)
_PULSE = "▁▂▃▄▅▆▇█▇▆▅▄▃▂"


class RainbowThrob:
    """Pulse a rainbow ``TOKENMAXXING`` banner while a long fan-out runs.

    Writes to STDERR only — never stdout (which is the MCP JSON-RPC channel). On a
    real TTY it animates in place; piped (the usual MCP-client case) it prints one
    plain start line + a done line so logs don't fill with escape codes.
    """

    def __init__(self, label: str):
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tty = bool(getattr(sys.stderr, "isatty", lambda: False)())

    def __enter__(self) -> RainbowThrob:
        if self._tty:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            sys.stderr.write(f"🌈 {self.label} …\n")
            sys.stderr.flush()
        return self

    def _run(self) -> None:
        for i in itertools.count():
            if self._stop.wait(0.1):
                break
            c = _ANSI_COLORS[i % len(_ANSI_COLORS)]
            p = _PULSE[i % len(_PULSE)]
            sys.stderr.write(f"\r\033[38;5;{c}m{p} 🌈 {self.label} {p}\033[0m\033[K")
            sys.stderr.flush()

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            sys.stderr.write("\r\033[K")  # clear the animated line
            sys.stderr.flush()
        elif not self._tty:
            sys.stderr.write(f"🌈 {self.label} — done\n")
            sys.stderr.flush()


def select_targets(
    pool: Pool,
    messages: list[dict],
    max_models=None,
    *,
    routing: str | None = None,
) -> tuple[list, int]:
    """Pick automatically eligible ranked targets across configured providers.

    Targets from :meth:`Pool.rank_targets` are interleaved round-robin by provider
    (best-first within each) so the swarm spans providers instead of pounding one
    provider's list. Active routing can narrow the ranked set, and callers such as
    CLI wise mode can narrow it further.

    Returns ``(picks, n_providers)``. ``max_models`` can only lower the count; the
    automatically eligible set is always capped at :data:`HARD_CAP` (256).
    """
    by_provider: dict[str, list] = {}
    for t in pool.rank_targets(messages, routing=routing):
        by_provider.setdefault(t.provider.id, []).append(t)
    interleaved = [t for tier in itertools.zip_longest(*by_provider.values()) for t in tier if t]
    default_limit = min(len(interleaved), HARD_CAP)
    if max_models is None:
        limit = default_limit
    else:
        try:
            limit = max(1, min(HARD_CAP, int(max_models)))
        except (TypeError, ValueError):
            limit = default_limit
    picks = interleaved[:limit]
    n_providers = len({t.provider.id for t in picks})
    return picks, n_providers


def fan_out(
    pool: Pool,
    messages: list[dict],
    picks: list,
    *,
    max_tokens: int,
    timeout: float = 90.0,
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[list[tuple[str, str | None]], list[str]]:
    """Blast ``messages`` to every target in ``picks`` concurrently.

    Calls ``progress(done, total, label)`` after each model returns (thread-safe).
    Returns ``(answered, failed)`` where ``answered`` is ``[(label, text)]`` and
    ``failed`` is ``[label]``.
    """
    total = len(picks)
    counter = itertools.count(1)
    lock = threading.Lock()

    def ask_one(t):
        try:
            r = pool.chat(
                messages,
                model=t.model,
                providers=[t.provider.id],
                max_tokens=max_tokens,
                timeout=timeout,
            )
            out = (f"{r.provider_id}/{r.model}", r.text, None)
        except Exception as exc:  # noqa: BLE001 — one model failing must not abort the swarm
            out = (f"{t.provider.id}/{t.model}", None, f"{type(exc).__name__}: {exc}")
        if progress is not None:
            with lock:
                done = next(counter)
            progress(done, total, out[0])
        return out

    if total == 0:
        return [], []
    with _cf.ThreadPoolExecutor(max_workers=min(WORKERS, total)) as ex:
        results = list(ex.map(ask_one, picks))
    answered = [(lbl, txt) for lbl, txt, err in results if not err]
    failed = [lbl for lbl, _txt, err in results if err]
    return answered, failed
