"""Async API: :class:`AsyncPool` mirrors :class:`~freellmpool.Pool` over httpx.AsyncClient.

    from freellmpool import AsyncPool

    async with AsyncPool.from_default_config() as pool:
        reply = await pool.aask("Explain CAP theorem in one sentence.")
        print(reply.text)

It shares the sync Pool's routing, quota, cooldown, metrics, and (opt-in) response
cache — only the HTTP I/O is async. A single ``httpx.AsyncClient`` is created lazily
and reused for the pool's lifetime; close it with ``await pool.aclose()`` or an
``async with``. If used across multiple event loops the client is recreated per loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable

from . import client as _client
from .capability import prompt_difficulty
from .client import (
    _CONNECT_TIMEOUT,
    _THINKING_FLOOR,
    _USER_AGENT,
    _err_message,
    _is_thinking,
    _retryable,
    _strip_think,
    _to_gemini_contents,
)
from .context import context_limit_from_error, estimate_input_tokens
from .errors import (
    AllProvidersExhausted,
    ContextWindowExceeded,
    NoProvidersConfigured,
    ProviderHTTPError,
)
from .models import Provider, Reply
from .observe import emit
from .router import Pool, _is_account_quota_exhaustion, _is_health_failure
from .routing_modes import normalize_routing_mode

#: An async transport: ``await apost(url, headers, json_body, timeout) -> HTTPResult``.
AsyncPostFn = Callable[[str, dict, dict, float], Awaitable["_client.HTTPResult"]]


async def _aread_capped_response(chunks, deadline: float, timeout: float) -> tuple[bytes, str]:
    out: list[bytes] = []
    total = 0
    loop = asyncio.get_running_loop()
    async for chunk in chunks:
        total += len(chunk)
        if total > _client._MAX_RESPONSE_BYTES:
            raise ProviderHTTPError(
                502,
                f"upstream response exceeded {_client._MAX_RESPONSE_BYTES} bytes",
                retryable=True,
            )
        if loop.time() > deadline:
            raise ProviderHTTPError(504, f"upstream exceeded {timeout:.0f}s deadline", retryable=True)
        out.append(chunk)
    raw = b"".join(out)
    return raw, raw.decode("utf-8", "replace")


class AsyncPool:
    """Async counterpart to :class:`~freellmpool.Pool`.

    Wraps a sync ``Pool`` for all configuration and bookkeeping; pass ``apost`` to
    inject a transport (the test suite does this to avoid the network).
    """

    def __init__(self, pool: Pool, *, apost: AsyncPostFn | None = None):
        self._pool = pool
        self._apost_fn = apost
        self._aclient = None  # lazy httpx.AsyncClient
        self._aclient_loop = None  # the loop the client is bound to
        self._aclient_lock = asyncio.Lock()  # serialize lazy create/close

    @classmethod
    def from_default_config(cls, **kwargs) -> AsyncPool:
        return cls(Pool.from_default_config(**kwargs))

    # ---- expose the underlying pool's config -------------------------
    @property
    def providers(self) -> list[Provider]:
        return self._pool.providers

    @property
    def metrics(self):
        return self._pool.metrics

    @property
    def env(self) -> dict[str, str]:
        return self._pool.env

    @property
    def stats(self) -> dict:
        return self._pool.stats

    @property
    def quota(self):
        return self._pool.quota

    # ---- client lifecycle --------------------------------------------
    async def _client_obj(self):
        import httpx

        running = asyncio.get_running_loop()
        async with self._aclient_lock:
            # An AsyncClient is bound to the loop that created it; if we're now on
            # a different loop (e.g. a second asyncio.run), drop the stale one.
            if self._aclient is not None and self._aclient_loop is not running:
                try:
                    await self._aclient.aclose()
                except Exception:  # noqa: BLE001 — old loop may be closed
                    pass
                self._aclient = None
            if self._aclient is None:
                self._aclient = httpx.AsyncClient(
                    headers={"User-Agent": _USER_AGENT},
                    limits=httpx.Limits(
                        max_keepalive_connections=20, max_connections=100, keepalive_expiry=30.0
                    ),
                    follow_redirects=True,
                )
                self._aclient_loop = running
            return self._aclient

    async def aclose(self) -> None:
        async with self._aclient_lock:
            if self._aclient is not None:
                await self._aclient.aclose()
                self._aclient = None
                self._aclient_loop = None

    async def __aenter__(self) -> AsyncPool:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def _apost(self, url: str, headers: dict, body: dict, timeout: float):
        if self._apost_fn is not None:
            return await self._apost_fn(url, headers, body, timeout)
        import httpx

        client = await self._client_obj()
        deadline = asyncio.get_running_loop().time() + timeout
        last_exc: httpx.HTTPError | None = None
        last_result: _client.HTTPResult | None = None
        for attempt in range(_client._MAX_TRANSPORT_ATTEMPTS):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                async with client.stream(
                    "POST",
                    url,
                    headers=headers,
                    json=body,
                    timeout=httpx.Timeout(remaining, connect=min(_CONNECT_TIMEOUT, remaining)),
                ) as resp:
                    raw, text = await _aread_capped_response(
                        resp.aiter_bytes(), deadline, remaining
                    )
                    status = resp.status_code
                    response_headers = dict(resp.headers)
            except httpx.HTTPError as exc:
                last_exc = exc
                if not _client._retryable_transport_error(exc, httpx):
                    raise
                if attempt + 1 >= _client._MAX_TRANSPORT_ATTEMPTS:
                    raise
                delay = _client._retry_delay_monotonic(None, attempt, deadline, asyncio.get_running_loop().time)
                if delay is None:
                    raise
                await asyncio.sleep(delay)
                continue
            result = _client._json_result(status, raw, text, headers=response_headers)
            last_result = result
            if _retryable(result.status) and attempt + 1 < _client._MAX_TRANSPORT_ATTEMPTS:
                delay = _client._retry_delay_monotonic(
                    result, attempt, deadline, asyncio.get_running_loop().time
                )
                if delay is not None:
                    await asyncio.sleep(delay)
                    continue
            return result
        if last_exc is not None:  # pragma: no cover - loop structure guard
            raise last_exc
        if last_result is not None:
            return last_result
        raise ProviderHTTPError(502, "transport retry loop exhausted", retryable=True)

    # ---- per-target async dispatch -----------------------------------
    async def _acall(
        self,
        provider: Provider,
        model: str,
        messages: list[dict],
        *,
        max_tokens: int,
        temperature: float,
        timeout: float,
        tools,
        tool_choice,
    ) -> Reply:
        if _is_thinking(model) and max_tokens < _THINKING_FLOOR:
            max_tokens = _THINKING_FLOOR
        api_key = provider.api_key(self.env)
        if provider.adapter == "gemini":
            if tools:
                raise ProviderHTTPError(
                    400, "gemini adapter does not support tools", retryable=True
                )
            return await self._acall_gemini(
                provider,
                model,
                messages,
                api_key=api_key,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
        if provider.adapter not in ("openai", "cloudflare"):
            # A plugin-registered (sync) adapter — run it off the event loop so it
            # behaves identically to the sync Pool. Unknown names fall through to
            # the native async openai shape (matching client._resolve_adapter).
            from .plugins import registered_adapters

            if provider.adapter in registered_adapters():
                return await asyncio.to_thread(
                    _client.call,
                    provider,
                    model,
                    messages,
                    api_key=api_key,
                    env=self.env,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                    tools=tools,
                    tool_choice=tool_choice,
                    post=self._pool._post,
                )
        return await self._acall_openai(
            provider,
            model,
            messages,
            api_key=api_key,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            tools=tools,
            tool_choice=tool_choice,
        )

    async def _acall_openai(
        self,
        provider,
        model,
        messages,
        *,
        api_key,
        max_tokens,
        temperature,
        timeout,
        tools,
        tool_choice,
    ) -> Reply:
        base_url = provider.base_url
        if provider.adapter == "cloudflare":
            base_url = base_url.replace("{account_id}", self.env.get("CLOUDFLARE_ACCOUNT_ID", ""))
        url = f"{base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        result = await self._apost(url, headers, body, timeout)
        if result.status != 200:
            raise ProviderHTTPError(
                result.status, _err_message(result), retryable=_retryable(result.status)
            )
        choices = result.body.get("choices") or []
        if not choices:
            raise ProviderHTTPError(502, "no choices in response", retryable=True)
        message = choices[0].get("message") or {}
        text = _strip_think(message.get("content") or "")
        usage = result.body.get("usage") or {}
        return Reply(
            text=text,
            provider_id=provider.id,
            model=model,
            raw=result.body,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            message=message if isinstance(message, dict) else None,
        )

    async def _acall_gemini(
        self,
        provider,
        model,
        messages,
        *,
        api_key,
        max_tokens,
        temperature,
        timeout,
    ) -> Reply:
        system_instruction, contents = _to_gemini_contents(messages)
        url = f"{provider.base_url}/models/{model}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        body: dict = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }
        if system_instruction:
            body["systemInstruction"] = system_instruction
        result = await self._apost(url, headers, body, timeout)
        if result.status != 200:
            raise ProviderHTTPError(
                result.status, _err_message(result), retryable=_retryable(result.status)
            )
        candidates = result.body.get("candidates") or []
        if not candidates:
            raise ProviderHTTPError(502, "no candidates in response", retryable=True)
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = _strip_think("".join(p.get("text", "") for p in parts))
        usage = result.body.get("usageMetadata") or {}
        return Reply(
            text=text,
            provider_id=provider.id,
            model=model,
            raw=result.body,
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
        )

    # ---- entrypoints --------------------------------------------------
    async def aask(self, prompt: str, *, system: str | None = None, **kwargs) -> Reply:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return await self.achat(messages, **kwargs)

    async def achat(
        self,
        messages: list[dict],
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
        """Async failover completion — same routing/cache/metrics as :meth:`Pool.chat`.

        ``timeout`` is one overall deadline shared by every failover attempt.
        """
        p = self._pool
        if not p.providers:
            raise NoProvidersConfigured("no provider has an API key set")
        provider_list = list(providers) if providers else None
        eff = normalize_routing_mode(routing, p.routing)

        cache_key = None
        if p._cache is not None:
            cache_key = p._cache.make_key(
                messages,
                model,
                provider_list,
                max_tokens,
                temperature,
                tools,
                tool_choice,
                eff,
            )
            hit = await asyncio.to_thread(p._cache.get, cache_key)  # blocking sqlite off-loop
            if hit is not None:
                emit(p._on_event, "cache_hit", key=cache_key)
                p._bump_stats(cache_hits=1)
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
            emit(p._on_event, "cache_miss", key=cache_key)

        difficulty = prompt_difficulty(messages, max_tokens, tools) if eff == "quality" else None
        targets = p._order(
            p._all_targets(include=provider_list, model=model), difficulty=difficulty, routing=eff
        )
        if not targets:
            raise NoProvidersConfigured("no candidate (provider, model) matched the given filters")

        now = p._clock()
        deadline = now + max(0.0, timeout)
        states = [
            (
                t,
                p._cooled(t.provider.id, now)
                or p._account_backed_off(t.provider.id, now),
            )
            for t in targets
        ]
        sequence = [t for t, c in states if not c] + [t for t, c in states if c]
        attempts: list[tuple[str, str]] = []
        unavailable_providers: set[str] = set()
        client_error: ProviderHTTPError | None = None
        est_tokens = estimate_input_tokens(messages, tools)
        needed = est_tokens + max_tokens
        ctx_overflow = False
        non_ctx_failure = False
        for target in sequence:
            if target.provider.id in unavailable_providers:
                attempts.append((target.name, "skipped (provider quota unavailable this request)"))
                continue
            api_key = target.provider.api_key(self.env)
            if api_key is None and not target.provider.keyless:
                non_ctx_failure = True
                attempts.append((target.name, "missing api key"))
                continue
            cap = p._effective_context(target)
            if cap is not None and needed > cap:
                attempts.append(
                    (target.name, f"skipped (context ~{cap} < needed ~{needed} tokens)")
                )
                emit(p._on_event, "context_skip", target=target.name, context=cap, needed=needed)
                ctx_overflow = True
                continue
            started = p._clock()
            remaining = deadline - started
            if remaining <= 0:
                attempts.append((target.name, "skipped (overall request timeout exhausted)"))
                break
            emit(p._on_event, "attempt", target=target.name, n=len(attempts) + 1)
            try:
                reply = await self._acall(
                    target.provider,
                    target.model,
                    messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=remaining,
                    tools=tools,
                    tool_choice=tool_choice,
                )
            except ProviderHTTPError as exc:
                is_ctx, limit = context_limit_from_error(exc.status, str(exc))
                if is_ctx:
                    ctx_overflow = True
                    if limit is not None:
                        p._learn_context_limit(target.name, limit)
                    emit(p._on_event, "error", target=target.name, reason=str(exc))
                    attempts.append(
                        (target.name, f"context window exceeded (limit ~{limit or '?'})")
                    )
                    continue
                if exc.status == 429:
                    p._mark_cooldown(target.provider.id, p._clock())
                    unavailable_providers.add(target.provider.id)
                    emit(p._on_event, "cooldown", target=target.name, status=429)
                account_exhausted = _is_account_quota_exhaustion(exc)
                if account_exhausted:
                    p._mark_account_backoff(target.provider.id, p._clock())
                    unavailable_providers.add(target.provider.id)
                # Any non-context failure (incl. a rate-limit, which might have fit)
                # means "too long" isn't provably the whole story — stay generic.
                non_ctx_failure = True
                if not exc.retryable and not account_exhausted and client_error is None:
                    client_error = exc
                if _is_health_failure(exc):
                    p.metrics.record_failure(target.name, str(exc))
                emit(p._on_event, "error", target=target.name, reason=str(exc))
                attempts.append((target.name, str(exc)))
                continue
            except Exception as exc:  # noqa: BLE001
                non_ctx_failure = True
                p.metrics.record_failure(target.name, f"{type(exc).__name__}: {exc}")
                emit(p._on_event, "error", target=target.name, reason=f"{type(exc).__name__}")
                attempts.append((target.name, f"{type(exc).__name__}: {exc}"))
                continue

            has_tool_calls = bool(reply.message and reply.message.get("tool_calls"))
            if not reply.text and not has_tool_calls:
                non_ctx_failure = True
                p.metrics.record_failure(target.name, "empty completion")
                emit(p._on_event, "error", target=target.name, reason="empty completion")
                attempts.append((target.name, "empty completion"))
                continue

            latency_ms = max(0.0, (p._clock() - started) * 1000.0)
            p.metrics.record_success(target.name, latency_ms)
            emit(
                p._on_event,
                "success",
                target=target.name,
                latency_ms=round(latency_ms, 1),
                attempts=len(attempts) + 1,
            )
            # Blocking flock/sqlite — run off the event loop so contention can't
            # stall other in-flight async requests.
            await asyncio.to_thread(p.quota.record, target.provider.id, target.model)
            reply.attempts = len(attempts) + 1
            p._bump_stats(
                requests=1,
                prompt_tokens=reply.prompt_tokens or 0,
                completion_tokens=reply.completion_tokens or 0,
            )
            if p._cache is not None and cache_key is not None:
                await asyncio.to_thread(
                    p._cache.put,
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
                emit(p._on_event, "cache_store", key=cache_key, target=target.name)
            return reply

        emit(p._on_event, "exhausted", attempts=len(attempts))
        if ctx_overflow and not non_ctx_failure:
            raise ContextWindowExceeded(attempts, est_tokens=est_tokens)
        if client_error is not None:
            raise AllProvidersExhausted(
                attempts, client_status=client_error.status, client_message=str(client_error)
            )
        raise AllProvidersExhausted(attempts)
