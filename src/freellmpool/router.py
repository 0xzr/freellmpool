"""The Pool: provider selection and failover.

A :class:`Pool` holds the configured providers, a quota store, and the
strategy for ordering candidate (provider, model) targets. :meth:`ask` walks
that ordered list, calling each target until one succeeds, recording quota use
and per-day budgets as it goes.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from . import client as _client
from .cache import Cache
from .capability import capability_table, fit_penalty, model_capability, prompt_difficulty
from .client import (
    MultipartPostFn,
    PostFn,
    StreamPostFn,
    default_multipart_post,
    default_post,
    default_stream_post,
)
from .config import (
    configured_embedders,
    configured_providers,
    configured_transcribers,
    effective_env,
    load_catalog,
    load_embedders,
    load_transcribers,
    settings,
)
from .context import context_limit_from_error, estimate_input_tokens
from .errors import (
    AllProvidersExhausted,
    ContextWindowExceeded,
    NoProvidersConfigured,
    ProviderHTTPError,
)
from .metrics import Metrics, score_stat
from .models import EmbedReply, Provider, Reply, TranscribeReply
from .observe import EventHook, emit
from .quota import QuotaStore
from .routing_modes import normalize_routing_mode
from .stats import StatsStore

# A parsed "context limit" below this is treated as garbled/implausible and not
# learned, so one bad provider error can't poison routing pool-wide.
_MIN_LEARNABLE_CONTEXT = 256
# Learned limits expire so a transient/edge provider error can't park a model for
# the whole process lifetime (providers drift; some report per-request limits).
_CTX_LIMIT_TTL = 1800.0  # seconds (30 min)
# Quality-routing latency tuning. A target whose smoothed latency reaches
# _QUALITY_SLOW_S counts as fully slow (penalty 1.0); interactive coding wants
# sub-handful-of-seconds, so 8s is "slow". An unmeasured target gets a neutral mid
# penalty (~2.7s-equivalent) so it is still sampled but loses to a proven-fast model.
# Both stay well under the capability under-power penalty (>=5), so latency only
# breaks ties among models that already clear the difficulty bar.
_QUALITY_SLOW_S = 8.0
_QUALITY_UNKNOWN_LAT = 0.34
# "spread" routing groups providers into coarse usage tiers of this many requests, so the
# least-used tier is served first (spreading load across the WHOLE pool to avoid one
# provider hitting its rate limit), while within a tier the fastest/healthiest is preferred.
# Small bucket => stronger spread; large => more latency-greedy. 8 balances both for
# sustained agentic loops on free tiers.
_SPREAD_BUCKET = 8


def _is_health_failure(exc: Exception) -> bool:
    """Whether an exception reflects provider *availability* (so it should count
    against the target's health metrics) vs a client/capability error that says
    nothing about whether the provider is up.

    429 / 408 / 5xx and raw network errors are availability failures. Other 4xx
    (400 bad request, 401/403 auth, 402 payment/capability, 404 unknown model,
    and the gemini "tools unsupported" 400) are not — counting them would let a
    tool request poison routing for later non-tool traffic.
    """
    if isinstance(exc, ProviderHTTPError):
        return exc.status in (429, 408) or exc.status >= 500
    return True  # connection error, timeout, etc.


@dataclass(frozen=True)
class Target:
    """A concrete (provider, model) pair the router can call."""

    provider: Provider
    model: str
    rpd: int
    context: int | None = None  # declared context-window size (tokens), if known

    @property
    def name(self) -> str:
        return f"{self.provider.id}/{self.model}"


@dataclass
class _ProviderOrderStats:
    """Precomputed routing stats for one provider in a candidate set."""

    targets: list[Target]
    used: int = 0
    all_over: bool = True
    all_failing: bool = True
    best_score: float = float("inf")


class Pool:
    def __init__(
        self,
        providers: list[Provider],
        *,
        quota: QuotaStore | None = None,
        env: dict[str, str] | None = None,
        post: PostFn = default_post,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] | None = None,
        embedders: list[Provider] | None = None,
        transcribers: list[Provider] | None = None,
        stream_post: StreamPostFn = default_stream_post,
        transcribe_post: MultipartPostFn = default_multipart_post,
        cache: Cache | None = None,
        metrics: Metrics | None = None,
        routing: str = "fair",
        on_event: EventHook | None = None,
        stats_store: StatsStore | None = None,
    ):
        self.providers = providers
        targets: list[Target] = []
        enabled_targets: list[Target] = []
        targets_by_provider: dict[str, list[Target]] = {}
        enabled_by_provider: dict[str, list[Target]] = {}
        targets_by_model: dict[str, list[Target]] = {}
        enabled_by_model: dict[str, list[Target]] = {}
        for provider in providers:
            for model_obj in provider.models:
                target = Target(provider, model_obj.name, model_obj.rpd, model_obj.context)
                targets.append(target)
                targets_by_provider.setdefault(provider.id, []).append(target)
                targets_by_model.setdefault(model_obj.name, []).append(target)
                if model_obj.enabled:
                    enabled_targets.append(target)
                    enabled_by_provider.setdefault(provider.id, []).append(target)
                    enabled_by_model.setdefault(model_obj.name, []).append(target)
        self._targets = tuple(targets)
        self._enabled_targets = tuple(enabled_targets)
        self._targets_by_provider = {k: tuple(v) for k, v in targets_by_provider.items()}
        self._enabled_targets_by_provider = {k: tuple(v) for k, v in enabled_by_provider.items()}
        self._targets_by_model = {k: tuple(v) for k, v in targets_by_model.items()}
        self._enabled_targets_by_model = {k: tuple(v) for k, v in enabled_by_model.items()}
        self.embedders = embedders or []
        self.transcribers = transcribers or []
        self._transcribe_post = transcribe_post
        self.quota = quota or QuotaStore()
        self.env = env if env is not None else dict(os.environ)
        self._post = post
        self._stream_post = stream_post
        self._cache = cache
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock or time.monotonic
        self.metrics = metrics or Metrics()
        # "fair"   — least-used provider first, then least-used model in provider.
        # "fast"   — lowest measured provider latency / failure penalty first.
        # "quality"— match prompt difficulty to model capability (benchmark-scored),
        #            so hard prompts get strong models and easy ones get light ones.
        # "legacy"/"model" keep the old per-(provider, model) balancing behavior.
        self.routing = normalize_routing_mode(routing)
        self._on_event = on_event
        # provider_id -> monotonic time until which to deprioritize after a 429
        self._cooldown_until: dict[str, float] = {}
        self._cooldown_lock = threading.Lock()
        # cumulative usage for the estimated-cost metric. `self.stats` is the
        # in-memory session counter; `_stats_store` (optional) persists lifetime
        # totals across restarts so the served-free / estimated-cost number grows.
        self.stats = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "cache_hits": 0}
        self._stats_store = stats_store
        self._stats_lock = threading.Lock()  # the proxy serves requests on many threads
        # Context-window limits learned from provider errors, keyed by provider/model.
        # Lets the pool stop routing oversized requests to models it has seen reject
        # them, without a hand-maintained per-model context table.
        self._ctx_limits: dict[str, tuple[int, float]] = {}  # name -> (limit, learned_at)
        self._ctx_lock = threading.Lock()

    def _bump_stats(self, **deltas: int) -> None:
        """Thread-safe read-modify-write of the session stats counters, plus a
        write-through to the persistent lifetime store (outside the in-memory lock
        so disk I/O never serializes request accounting)."""
        with self._stats_lock:
            for key, delta in deltas.items():
                self.stats[key] = self.stats.get(key, 0) + delta
        if self._stats_store is not None:
            self._stats_store.add(**deltas)

    def stats_snapshot(self) -> dict:
        """A consistent copy of the session stats counters, read under the lock so
        readers (/status, MCP, CLI) never see a torn requests/tokens pair."""
        with self._stats_lock:
            return dict(self.stats)

    def cooldown_snapshot(self, now: float) -> dict[str, float]:
        """provider_id -> seconds remaining on cooldown, read under the lock."""
        with self._cooldown_lock:
            return {pid: max(0.0, until - now) for pid, until in self._cooldown_until.items()}

    def lifetime_stats(self) -> dict:
        """Persistent lifetime totals (+ first_seen), or the in-memory session
        totals if no persistent store is wired."""
        if self._stats_store is not None:
            return self._stats_store.snapshot()
        return {**self.stats_snapshot(), "first_seen": None}

    def rank_targets(
        self,
        messages: list[dict[str, str]],
        *,
        routing: str | None = None,
        model: str | None = None,
        providers: Iterable[str] | None = None,
    ) -> list[Target]:
        """Public: the ordered candidate (provider, model) targets for ``messages``,
        using the same ordering the failover loop would. Powers the MCP route
        explainer and multi-model panel (which fan out across the top targets) without
        re-implementing routing. Read-only — does not call any provider."""
        provider_list = list(providers) if providers else None
        eff = normalize_routing_mode(routing, self.routing)
        difficulty = prompt_difficulty(messages) if eff == "quality" else None
        return self._order(
            self._all_targets(include=provider_list, model=model),
            difficulty=difficulty,
            routing=eff,
        )

    def _mark_cooldown(self, provider_id: str, now: float) -> None:
        until = now + self.cooldown_seconds
        with self._cooldown_lock:
            self._cooldown_until[provider_id] = max(
                self._cooldown_until.get(provider_id, 0.0), until
            )

    def _cooled(self, provider_id: str, now: float) -> bool:
        with self._cooldown_lock:
            return self._cooldown_until.get(provider_id, 0.0) > now

    # ---- context-window awareness -------------------------------------

    def _effective_context(self, target: Target) -> int | None:
        """The tightest known context window for a target: the smaller of its
        declared size and any (non-expired) limit learned from a prior error."""
        learned = None
        with self._ctx_lock:
            entry = self._ctx_limits.get(target.name)
        if entry is not None and self._clock() - entry[1] < _CTX_LIMIT_TTL:
            learned = entry[0]
        sizes = [v for v in (target.context, learned) if v is not None]
        return min(sizes) if sizes else None

    def _learn_context_limit(self, target_name: str, limit: int) -> None:
        """Record a context-window limit revealed by a provider error (tighter wins,
        with a TTL). Implausibly small figures are ignored so a garbled error can't
        park a model."""
        if limit < _MIN_LEARNABLE_CONTEXT:
            return
        now = self._clock()
        with self._ctx_lock:
            prev = self._ctx_limits.get(target_name)
            # Keep a still-fresh, equal-or-tighter prior untouched — so a looser (or
            # repeated) value can't keep refreshing the tight limit's clock and defeat
            # the TTL. Only a strictly tighter new observation (or an expired/absent
            # prior) updates the entry and its timestamp.
            if prev is not None and now - prev[1] < _CTX_LIMIT_TTL and prev[0] <= limit:
                return
            self._ctx_limits[target_name] = (limit, now)

    # ---- construction -------------------------------------------------

    @classmethod
    def from_default_config(
        cls,
        *,
        env: dict[str, str] | None = None,
        quota: QuotaStore | None = None,
        post: PostFn = default_post,
        on_event: EventHook | None = None,
    ) -> Pool:
        from .plugins import registered_providers  # lazy: avoids import cycle

        # Merge config.toml [keys] underneath the real environment.
        env = effective_env(env)
        # Merge plugin providers by id (a plugin reusing a built-in id overrides
        # it, same as user-catalog overrides) — never two providers with one id,
        # which would split quota/metrics/cooldown.
        by_id = {p.id: p for p in load_catalog()}
        for p in registered_providers():
            by_id[p.id] = p
        catalog = list(by_id.values())
        providers = configured_providers(catalog, env)
        embedders = configured_embedders(load_embedders(), env)
        transcribers = configured_transcribers(load_transcribers(), env)
        cfg = settings(env)
        cooldown = float(cfg.get("cooldown_seconds", 60.0))
        ttl = float(env.get("FREELLMPOOL_CACHE_TTL") or cfg.get("cache_ttl", 0) or 0)
        cache = Cache(ttl) if ttl > 0 else None
        from .mode import default_routing_for_mode

        routing = default_routing_for_mode(env, cfg)
        return cls(
            providers,
            quota=quota,
            env=env,
            post=post,
            cooldown_seconds=cooldown,
            embedders=embedders,
            transcribers=transcribers,
            cache=cache,
            routing=routing,
            on_event=on_event,
            stats_store=StatsStore(),
        )

    def embed(
        self,
        texts: str | list[str],
        *,
        model: str | None = None,
        providers: Iterable[str] | None = None,
        timeout: float = 90.0,
    ) -> EmbedReply:
        """Embed one or more texts, failing over across configured embedders."""
        inputs = [texts] if isinstance(texts, str) else list(texts)
        if not self.embedders:
            raise NoProvidersConfigured(
                "no embedder configured; set a key for one of: cohere, github, "
                "cloudflare, mistral, nvidia (see docs/ACCOUNTS.md)"
            )
        include = {p.strip() for p in providers} if providers else None
        attempts: list[tuple[str, str]] = []
        for emb in self.embedders:
            if include is not None and emb.id not in include:
                continue
            for m in emb.models:
                if not m.enabled:
                    continue
                if model is not None and m.name != model:
                    continue
                try:
                    reply = _client.embed(
                        emb,
                        m.name,
                        inputs,
                        api_key=emb.api_key(self.env),
                        env=self.env,
                        timeout=timeout,
                        post=self._post,
                    )
                except Exception as exc:  # noqa: BLE001 — try the next embedder
                    attempts.append((f"{emb.id}/{m.name}", f"{type(exc).__name__}: {exc}"))
                    continue
                # Account for embeddings like chat: per-day quota + lifetime stats, so
                # a heavy embedding workload doesn't bypass RPD pacing / usage totals.
                self.quota.record(emb.id, m.name)
                self._bump_stats(requests=1, prompt_tokens=reply.prompt_tokens or 0)
                return reply
        if not attempts:  # provider/model pins matched no configured embedder
            raise NoProvidersConfigured("no candidate embedder/model matched the given filters")
        raise AllProvidersExhausted(attempts)

    def transcribe(
        self,
        audio: bytes,
        filename: str,
        *,
        model: str | None = None,
        providers: Iterable[str] | None = None,
        language: str | None = None,
        response_format: str = "json",
        timeout: float = 90.0,
    ) -> TranscribeReply:
        """Transcribe audio→text, failing over across configured transcribers (Whisper)."""
        if not self.transcribers:
            raise NoProvidersConfigured(
                "no transcriber configured; set a key for one of: groq (see docs/ACCOUNTS.md)"
            )
        include = {p.strip() for p in providers} if providers else None
        attempts: list[tuple[str, str]] = []
        for tr in self.transcribers:
            if include is not None and tr.id not in include:
                continue
            for m in tr.models:
                if not m.enabled:
                    continue
                if model is not None and m.name != model:
                    continue
                try:
                    reply = _client.transcribe(
                        tr,
                        m.name,
                        audio,
                        filename,
                        api_key=tr.api_key(self.env),
                        env=self.env,
                        language=language,
                        response_format=response_format,
                        timeout=timeout,
                        post=self._transcribe_post,
                    )
                except Exception as exc:  # noqa: BLE001 — try the next transcriber
                    attempts.append((f"{tr.id}/{m.name}", f"{type(exc).__name__}: {exc}"))
                    continue
                self.quota.record(tr.id, m.name)
                self._bump_stats(requests=1, prompt_tokens=reply.prompt_tokens or 0)
                return reply
        if not attempts:  # provider/model pins matched no configured transcriber
            raise NoProvidersConfigured("no candidate transcriber/model matched the given filters")
        raise AllProvidersExhausted(attempts)

    # ---- candidate ordering -------------------------------------------

    def _all_targets(
        self,
        include: Iterable[str] | None = None,
        model: str | None = None,
    ) -> list[Target]:
        include_set = {p.strip() for p in include} if include else None
        source = self._targets_by_model.get(model, ()) if model is not None else self._enabled_targets
        if include_set is None:
            return list(source)
        return [target for target in source if target.provider.id in include_set]

    def _order(
        self, targets: list[Target], difficulty: float | None = None, routing: str | None = None
    ) -> list[Target]:
        """Order candidate targets for failover.

        ``fair`` (default): least-used provider first, then least-used model inside
        that provider. This prevents wide catalogs from getting extra traffic only
        because they expose more models.

        ``fast``: lowest measured provider latency / failure penalty first, then
        least-used provider.

        ``spread``: least-used *tier* first (usage bucketed by ``_SPREAD_BUCKET``) so load
        spreads across the WHOLE pool — no single provider hits its rate limit first — then
        fastest/healthiest within a tier. Best for sustained agentic loops on free tiers:
        the breadth of ``fair`` with the speed of ``fast``.

        ``quality``: match the request's ``difficulty`` (0–1) to each model's
        benchmark-scored capability — strong models for hard prompts, light models
        for easy ones (rationing scarce strong-model quota). Ordered globally, not
        provider-grouped, since capability match is the opted-in intent.

        ``legacy``/``model`` preserve the previous per-target balancing. Either way
        the ordering is a hint — failover still reaches all.
        """

        # One quota and metrics snapshot instead of locked reads per target (matters
        # for large catalogs and route explanations).
        snap = self.quota.snapshot()
        metrics = self.metrics
        msnap = metrics.snapshot()
        mode = normalize_routing_mode(routing, self.routing)

        def used_of(t: Target) -> int:
            return int(snap.get(f"{t.provider.id}::{t.model}", 0))

        def over_of(t: Target) -> int:
            return 1 if (t.rpd > 0 and used_of(t) >= t.rpd) else 0

        def stat_of(t: Target):
            return msnap.get(t.name)

        def failing_of(t: Target) -> bool:
            st = stat_of(t)
            return bool(st and st.failing)

        def score_of(t: Target) -> float:
            return score_stat(stat_of(t))

        if mode == "quality":
            table = capability_table()
            need = difficulty if difficulty is not None else 0.5

            def lat_pen(t: Target) -> float:
                # Latency penalty in [0,1]: a measured target's smoothed latency
                # scaled so anything >= _QUALITY_SLOW_S counts as fully slow; an
                # unmeasured one gets a neutral mid value so it is still sampled.
                # Because the capability under-power penalty is >=5 for any model
                # below the difficulty bar, this term only re-orders models that
                # *already clear* the bar — so quality stops parking on a giant that
                # takes tens of seconds when a comparably-capable model answers in ~1s.
                st = stat_of(t)
                if st is None or st.ewma_ms is None:
                    return _QUALITY_UNKNOWN_LAT
                return min(st.ewma_ms / 1000.0, _QUALITY_SLOW_S) / _QUALITY_SLOW_S

            def quality_key(t: Target) -> tuple[int, int, float, int]:
                # over-budget, then known-failing sink to the back (still reachable);
                # then capability-fit blended with latency; then least-used.
                return (
                    over_of(t),
                    1 if failing_of(t) else 0,
                    fit_penalty(model_capability(t.model, table), need) + lat_pen(t),
                    used_of(t),
                )

            return sorted(targets, key=quality_key)

        if mode in ("legacy", "model", "model-fast"):

            def legacy_fast_key(t: Target) -> tuple[int, float, int]:
                return (over_of(t), score_of(t), used_of(t))

            def legacy_fair_key(t: Target) -> tuple[int, int, int]:
                return (over_of(t), 1 if failing_of(t) else 0, used_of(t))

            key = legacy_fast_key if mode == "model-fast" else legacy_fair_key
            return sorted(targets, key=key)

        by_provider: dict[str, _ProviderOrderStats] = {}
        for target in targets:
            provider_id = target.provider.id
            stats = by_provider.setdefault(provider_id, _ProviderOrderStats(targets=[]))
            stats.targets.append(target)
            target_used = used_of(target)
            target_over = over_of(target)
            target_failing = failing_of(target)
            stats.used += target_used
            stats.all_over = stats.all_over and bool(target_over)
            stats.all_failing = stats.all_failing and target_failing
            stats.best_score = min(stats.best_score, score_of(target))

        def target_fair_key(t: Target) -> tuple[int, int, int]:
            return (over_of(t), 1 if failing_of(t) else 0, used_of(t))

        def target_fast_key(t: Target) -> tuple[int, float, int]:
            return (over_of(t), score_of(t), used_of(t))

        def target_spread_key(t: Target) -> tuple[int, int, int, float]:
            # least-used TIER first (spread across the whole pool), then fastest within tier.
            return (
                over_of(t),
                1 if failing_of(t) else 0,
                used_of(t) // _SPREAD_BUCKET,
                score_of(t),
            )

        if mode == "fast":

            def provider_fast_key(provider_id: str) -> tuple[int, float, int]:
                stats = by_provider[provider_id]
                return (1 if stats.all_over else 0, stats.best_score, stats.used)

            provider_order = sorted(by_provider, key=provider_fast_key)
            target_key = target_fast_key
        elif mode == "spread":

            def provider_spread_key(provider_id: str) -> tuple[int, int, int, float]:
                # group providers into usage tiers (least-used tier first → load spreads
                # across ALL providers), then prefer the fastest/healthiest within a tier.
                stats = by_provider[provider_id]
                return (
                    1 if stats.all_over else 0,
                    1 if stats.all_failing else 0,
                    stats.used // _SPREAD_BUCKET,
                    stats.best_score,
                )

            provider_order = sorted(by_provider, key=provider_spread_key)
            target_key = target_spread_key
        else:

            def provider_fair_key(provider_id: str) -> tuple[int, int, int]:
                stats = by_provider[provider_id]
                return (
                    1 if stats.all_over else 0,
                    1 if stats.all_failing else 0,
                    stats.used,
                )

            provider_order = sorted(by_provider, key=provider_fair_key)
            target_key = target_fair_key

        ordered: list[Target] = []
        for provider_id in provider_order:
            ordered.extend(sorted(by_provider[provider_id].targets, key=target_key))
        return ordered

    # ---- the main entrypoint ------------------------------------------

    def ask(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        providers: Iterable[str] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        timeout: float = 90.0,
        tools: list | None = None,
        tool_choice=None,
        routing: str | None = None,
    ) -> Reply:
        """Send ``prompt`` to the first provider that succeeds.

        ``model`` / ``providers`` optionally restrict the candidate set.
        ``routing`` overrides the pool's default routing mode for this request.
        Raises :class:`NoProvidersConfigured` if nothing is usable, or
        :class:`AllProvidersExhausted` if every candidate failed.
        """
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(
            messages,
            model=model,
            providers=providers,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            tools=tools,
            tool_choice=tool_choice,
            routing=routing,
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        providers: Iterable[str] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        timeout: float = 90.0,
        tools: list | None = None,
        tool_choice=None,
        routing: str | None = None,
    ) -> Reply:
        """Like :meth:`ask` but takes raw OpenAI-style ``messages``."""
        if not self.providers:
            raise NoProvidersConfigured(
                "no provider has an API key set; see .env.example for the env vars"
            )

        provider_list = list(providers) if providers else None
        # Resolve the effective routing mode *before* the cache key so that a
        # per-request override (fast/quality/fair/…) keys its own cache bucket and
        # never serves a reply produced under a different mode's intent.
        eff = normalize_routing_mode(routing, self.routing)
        cache_key = None
        if self._cache is not None:
            cache_key = self._cache.make_key(
                messages, model, provider_list, max_tokens, temperature, tools, tool_choice, eff
            )
            hit = self._cache.get(cache_key)
            if hit is not None:
                emit(self._on_event, "cache_hit", key=cache_key)
                self._bump_stats(cache_hits=1)
                return Reply(
                    text=hit.get("text", ""),
                    provider_id=hit.get("provider_id", "cache"),
                    model=hit.get("model", "?"),
                    raw={},
                    prompt_tokens=hit.get("prompt_tokens"),
                    completion_tokens=hit.get("completion_tokens"),
                    message=hit.get("message"),
                    cached=True,
                )
            emit(self._on_event, "cache_miss", key=cache_key)

        difficulty = prompt_difficulty(messages, max_tokens, tools) if eff == "quality" else None
        targets = self._order(
            self._all_targets(include=provider_list, model=model),
            difficulty=difficulty,
            routing=eff,
        )
        if not targets:
            raise NoProvidersConfigured("no candidate (provider, model) matched the given filters")

        # Providers recently rate-limited (429) are tried last, not skipped — so
        # a transient cooldown never makes a request fail outright. Read each
        # target's cooldown state exactly once so a concurrent 429 can't place the
        # same target in both buckets.
        now = self._clock()
        states = [(t, self._cooled(t.provider.id, now)) for t in targets]
        sequence = [t for t, c in states if not c] + [t for t, c in states if c]

        attempts: list[tuple[str, str]] = []
        rate_limited: set[str] = set()  # providers that 429'd during THIS request
        client_error: ProviderHTTPError | None = None  # first non-retryable 4xx seen
        # Context-window awareness: estimate the request size once, skip models we
        # already know are too small, and fail loudly if nothing fits.
        est_tokens = estimate_input_tokens(messages, tools)
        needed = est_tokens + max_tokens
        ctx_overflow = False
        non_ctx_failure = False
        for target in sequence:
            if target.provider.id in rate_limited:
                # Already 429'd this request — don't waste calls on its other models.
                attempts.append((target.name, "skipped (provider rate-limited this request)"))
                continue
            api_key = target.provider.api_key(self.env)
            if api_key is None and not target.provider.keyless:  # pragma: no cover
                non_ctx_failure = True
                attempts.append((target.name, "missing api key"))
                continue
            cap = self._effective_context(target)
            if cap is not None and needed > cap:
                attempts.append(
                    (target.name, f"skipped (context ~{cap} < needed ~{needed} tokens)")
                )
                emit(self._on_event, "context_skip", target=target.name, context=cap, needed=needed)
                ctx_overflow = True
                continue
            emit(self._on_event, "attempt", target=target.name, n=len(attempts) + 1)
            started = self._clock()
            try:
                reply = _client.call(
                    target.provider,
                    target.model,
                    messages,
                    api_key=api_key,
                    env=self.env,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                    tools=tools,
                    tool_choice=tool_choice,
                    post=self._post,
                )
            except ProviderHTTPError as exc:
                is_ctx, limit = context_limit_from_error(exc.status, str(exc))
                if is_ctx:
                    ctx_overflow = True
                    if limit is not None:
                        self._learn_context_limit(target.name, limit)
                    emit(self._on_event, "error", target=target.name, reason=str(exc))
                    attempts.append(
                        (target.name, f"context window exceeded (limit ~{limit or '?'})")
                    )
                    continue
                if exc.status == 429:
                    self._mark_cooldown(target.provider.id, self._clock())
                    rate_limited.add(target.provider.id)
                    emit(self._on_event, "cooldown", target=target.name, status=429)
                # Any non-context failure (incl. a rate-limit, which might have fit)
                # means "too long" isn't provably the whole story — stay generic.
                non_ctx_failure = True
                # A non-retryable 4xx usually means the request itself is invalid;
                # remember the first one so an exhausted pool can surface the real
                # client error instead of a generic 502. (Failover still proceeds —
                # another provider may accept it.)
                if not exc.retryable and client_error is None:
                    client_error = exc
                if _is_health_failure(exc):
                    self.metrics.record_failure(target.name, str(exc))
                emit(self._on_event, "error", target=target.name, reason=str(exc))
                attempts.append((target.name, str(exc)))
                continue
            except Exception as exc:  # network error, etc. — try the next one
                non_ctx_failure = True
                self.metrics.record_failure(target.name, f"{type(exc).__name__}: {exc}")
                emit(self._on_event, "error", target=target.name, reason=f"{type(exc).__name__}")
                attempts.append((target.name, f"{type(exc).__name__}: {exc}"))
                continue

            has_tool_calls = bool(reply.message and reply.message.get("tool_calls"))
            if not reply.text and not has_tool_calls:
                non_ctx_failure = True
                self.metrics.record_failure(target.name, "empty completion")
                emit(self._on_event, "error", target=target.name, reason="empty completion")
                attempts.append((target.name, "empty completion"))
                continue

            latency_ms = max(0.0, (self._clock() - started) * 1000.0)
            self.metrics.record_success(target.name, latency_ms)
            emit(
                self._on_event,
                "success",
                target=target.name,
                latency_ms=round(latency_ms, 1),
                attempts=len(attempts) + 1,
            )
            self.quota.record(target.provider.id, target.model)
            reply.attempts = len(attempts) + 1
            self._bump_stats(
                requests=1,
                prompt_tokens=reply.prompt_tokens or 0,
                completion_tokens=reply.completion_tokens or 0,
            )
            if self._cache is not None and cache_key is not None:
                self._cache.put(
                    cache_key,
                    {
                        "text": reply.text,
                        "provider_id": reply.provider_id,
                        "model": reply.model,
                        "prompt_tokens": reply.prompt_tokens,
                        "completion_tokens": reply.completion_tokens,
                        "message": reply.message,
                    },
                )
                emit(self._on_event, "cache_store", key=cache_key, target=target.name)
            return reply

        emit(self._on_event, "exhausted", attempts=len(attempts))
        if ctx_overflow and not non_ctx_failure:
            raise ContextWindowExceeded(attempts, est_tokens=est_tokens)
        if client_error is not None:
            raise AllProvidersExhausted(
                attempts, client_status=client_error.status, client_message=str(client_error)
            )
        raise AllProvidersExhausted(attempts)

    def stream_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        providers: Iterable[str] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        timeout: float = 90.0,
        routing: str | None = None,
    ):
        """Stream content deltas with token-level streaming.

        Yields a meta dict ``{"provider", "model"}`` first, then content-delta
        strings. Failover happens *before* the first token (on a non-200); once
        tokens flow, freellmpool is committed to that provider. Gemini-adapter
        providers are skipped (no OpenAI-shape stream).
        """
        if not self.providers:
            raise NoProvidersConfigured("no provider has an API key set")
        eff = normalize_routing_mode(routing, self.routing)
        difficulty = prompt_difficulty(messages, max_tokens) if eff == "quality" else None
        targets = self._order(
            self._all_targets(include=providers, model=model),
            difficulty=difficulty,
            routing=eff,
        )
        targets = [t for t in targets if t.provider.adapter != "gemini"]
        if not targets:
            raise NoProvidersConfigured("no streamable (provider, model) matched the filters")

        now = self._clock()
        states = [(t, self._cooled(t.provider.id, now)) for t in targets]
        sequence = [t for t, c in states if not c] + [t for t, c in states if c]
        attempts: list[tuple[str, str]] = []
        rate_limited: set[str] = set()
        est_tokens = estimate_input_tokens(messages)
        needed = est_tokens + max_tokens
        ctx_overflow = False
        non_ctx_failure = False
        for target in sequence:
            if target.provider.id in rate_limited:
                attempts.append((target.name, "skipped (provider rate-limited this request)"))
                continue
            api_key = target.provider.api_key(self.env)
            if api_key is None and not target.provider.keyless:
                non_ctx_failure = True
                attempts.append((target.name, "missing api key"))
                continue
            cap = self._effective_context(target)
            if cap is not None and needed > cap:
                attempts.append(
                    (target.name, f"skipped (context ~{cap} < needed ~{needed} tokens)")
                )
                emit(self._on_event, "context_skip", target=target.name, context=cap, needed=needed)
                ctx_overflow = True
                continue
            emit(self._on_event, "attempt", target=target.name, stream=True)
            started = self._clock()
            gen = _client.stream_call(
                target.provider,
                target.model,
                messages,
                api_key=api_key,
                env=self.env,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
                stream_post=self._stream_post,
            )
            try:
                first = next(gen)  # triggers connection + status check
            except StopIteration:
                non_ctx_failure = True
                self.metrics.record_failure(target.name, "empty stream")
                emit(self._on_event, "error", target=target.name, reason="empty stream")
                attempts.append((target.name, "empty stream"))
                continue
            except ProviderHTTPError as exc:
                is_ctx, limit = context_limit_from_error(exc.status, str(exc))
                if is_ctx:
                    ctx_overflow = True
                    if limit is not None:
                        self._learn_context_limit(target.name, limit)
                    emit(self._on_event, "error", target=target.name, reason=str(exc))
                    attempts.append(
                        (target.name, f"context window exceeded (limit ~{limit or '?'})")
                    )
                    continue
                if exc.status == 429:
                    self._mark_cooldown(target.provider.id, self._clock())
                    rate_limited.add(target.provider.id)
                    emit(self._on_event, "cooldown", target=target.name, status=429)
                # Any non-context failure (incl. a rate-limit, which might have fit)
                # means "too long" isn't provably the whole story — stay generic.
                non_ctx_failure = True
                if _is_health_failure(exc):
                    self.metrics.record_failure(target.name, str(exc))
                emit(self._on_event, "error", target=target.name, reason=str(exc))
                attempts.append((target.name, str(exc)))
                continue
            except Exception as exc:  # noqa: BLE001
                non_ctx_failure = True
                self.metrics.record_failure(target.name, f"{type(exc).__name__}: {exc}")
                emit(self._on_event, "error", target=target.name, reason=f"{type(exc).__name__}")
                attempts.append((target.name, f"{type(exc).__name__}: {exc}"))
                continue

            # First byte arrived — count it a success (latency to first token).
            latency_ms = max(0.0, (self._clock() - started) * 1000.0)
            self.metrics.record_success(target.name, latency_ms)
            emit(
                self._on_event,
                "success",
                target=target.name,
                latency_ms=round(latency_ms, 1),
                stream=True,
            )
            self.quota.record(target.provider.id, target.model)
            self._bump_stats(requests=1)
            yield {"provider": target.provider.id, "model": target.model}
            # Accumulate the streamed text so we can record token usage when the stream
            # ends. Providers rarely send a usage block on a stream, so without this the
            # session + lifetime token / savings totals — and the dashboard's tok/s — never
            # move for streaming clients (e.g. OpenCode). chars/4 ≈ tokens, matching
            # estimate_tokens; prompt size reuses est_tokens computed above.
            streamed: list[str] = [first] if isinstance(first, str) else []
            drained = False
            try:
                yield first
                for chunk in gen:
                    if isinstance(chunk, str):
                        streamed.append(chunk)
                    yield chunk
                drained = True
            finally:
                # The manual loop (vs `yield from`) doesn't auto-delegate close(), so on an
                # early consumer disconnect we must close the upstream stream ourselves to
                # release the provider HTTP connection promptly. Guard for iterators that
                # aren't generators. Only count a fully-drained stream — a disconnected/
                # truncated one isn't a completed response.
                closer = getattr(gen, "close", None)
                if callable(closer):
                    closer()
                if drained:
                    self._bump_stats(
                        prompt_tokens=max(0, est_tokens),
                        completion_tokens=max(0, sum(len(s) for s in streamed) // 4),
                    )
            return

        emit(self._on_event, "exhausted", attempts=len(attempts))
        if ctx_overflow and not non_ctx_failure:
            raise ContextWindowExceeded(attempts, est_tokens=est_tokens)
        raise AllProvidersExhausted(attempts)
