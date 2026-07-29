"""Shared bounded multi-model panel helpers.

This module powers the "second opinion" surfaces: CLI, MCP, and future battle
or recipe flows. It is deliberately smaller than tokenmax: panels ask a few
diverse models, keep structured per-model records, and treat synthesis as a
non-fatal bonus.
"""

from __future__ import annotations

import concurrent.futures as _cf
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .capability import normalize_model_name
from .router import Pool

DEFAULT_PANEL_COUNT = 3
MIN_PANEL_COUNT = 2
MAX_PANEL_COUNT = 5
DEFAULT_MAX_TOKENS = 512
MIN_MAX_TOKENS = 1
MAX_MAX_TOKENS = 8192
DEFAULT_ROUTING = "quality"
DEFAULT_TIMEOUT = 90.0
_WORKERS = 8


@dataclass(frozen=True)
class PanelAnswer:
    provider_id: str
    model: str
    label: str
    family: str | None
    text: str | None
    latency_ms: int
    error: str | None = None
    cached: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and self.text is not None


@dataclass(frozen=True)
class PanelSynthesis:
    provider_id: str | None
    model: str | None
    text: str | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.text is not None


@dataclass(frozen=True)
class PanelResult:
    prompt: str
    answers: tuple[PanelAnswer, ...]
    requested_count: int
    selected_count: int
    max_tokens: int
    truncated: bool = False
    synthesis: PanelSynthesis | None = None

    @property
    def successful_answers(self) -> tuple[PanelAnswer, ...]:
        return tuple(answer for answer in self.answers if answer.ok)


def clamp_panel_count(value: object, default: int = DEFAULT_PANEL_COUNT) -> int:
    return _clamp_int(value, default, MIN_PANEL_COUNT, MAX_PANEL_COUNT)


def clamp_max_tokens(value: object, default: int = DEFAULT_MAX_TOKENS) -> int:
    return _clamp_int(value, default, MIN_MAX_TOKENS, MAX_MAX_TOKENS)


def messages_from_prompt(prompt: str, system: str | None = None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system.strip()})
    messages.append({"role": "user", "content": prompt})
    return messages


def select_panel_targets(
    pool: Pool,
    messages: list[dict[str, str]],
    *,
    n: object = DEFAULT_PANEL_COUNT,
    routing: str | None = DEFAULT_ROUTING,
    model: str | None = None,
    providers: Iterable[str] | None = None,
    task: str | None = None,
) -> list[Any]:
    """Pick a small, diverse set of targets for a second-opinion panel.

    The selector first prefers one target per provider and, when two or more
    model families are detectable, one target per family. If all candidates
    collapse to one family or no family can be detected, it falls back to the
    existing distinct-provider behavior used by the old MCP panel.
    """

    limit = clamp_panel_count(n)
    candidates = pool.rank_targets(
        messages,
        routing=routing,
        model=model,
        providers=providers,
        task=task,
    )
    if not candidates:
        return []

    known_families = {family for target in candidates if (family := target_family(target))}
    use_family_diversity = len(known_families) >= 2
    selected: list[Any] = []
    seen_targets: set[tuple[str, str]] = set()
    seen_providers: set[str] = set()
    seen_families: set[str] = set()

    def append(target: Any, *, require_new_provider: bool, require_new_family: bool) -> bool:
        provider_id = target.provider.id
        model_name = target.model
        key = (provider_id, model_name)
        family = target_family(target)
        if key in seen_targets:
            return False
        if require_new_provider and provider_id in seen_providers:
            return False
        if require_new_family and family is not None and family in seen_families:
            return False
        selected.append(target)
        seen_targets.add(key)
        seen_providers.add(provider_id)
        if family is not None:
            seen_families.add(family)
        return len(selected) >= limit

    passes = (
        (True, use_family_diversity),
        (True, False),
        (False, use_family_diversity),
        (False, False),
    )
    for require_new_provider, require_new_family in passes:
        for target in candidates:
            if append(
                target,
                require_new_provider=require_new_provider,
                require_new_family=require_new_family,
            ):
                return selected
    return selected


def run_panel(
    pool: Pool,
    *,
    prompt: str,
    messages: list[dict[str, str]] | None = None,
    system: str | None = None,
    n: object = DEFAULT_PANEL_COUNT,
    routing: str | None = DEFAULT_ROUTING,
    model: str | None = None,
    providers: Iterable[str] | None = None,
    max_tokens: object = DEFAULT_MAX_TOKENS,
    timeout: float = DEFAULT_TIMEOUT,
    synthesize: bool = False,
    task: str | None = None,
) -> PanelResult:
    requested_count = _int_or_default(n, DEFAULT_PANEL_COUNT)
    selected_count = clamp_panel_count(n)
    token_limit = clamp_max_tokens(max_tokens)
    truncated = requested_count != selected_count or _int_or_default(max_tokens, token_limit) != token_limit
    msgs = messages if messages is not None else messages_from_prompt(prompt, system)
    picks = select_panel_targets(
        pool,
        msgs,
        n=selected_count,
        routing=routing,
        model=model,
        providers=providers,
        task=task,
    )
    if not picks:
        return PanelResult(
            prompt=prompt,
            answers=(),
            requested_count=requested_count,
            selected_count=0,
            max_tokens=token_limit,
            truncated=truncated,
        )

    def ask_one(target: Any) -> PanelAnswer:
        started = time.monotonic()
        provider_id = target.provider.id
        model_name = target.model
        try:
            reply = pool.chat(
                msgs,
                model=model_name,
                providers=[provider_id],
                max_tokens=token_limit,
                timeout=timeout,
                task=task,
            )
            latency_ms = round((time.monotonic() - started) * 1000)
            label = f"{reply.provider_id}/{reply.model}"
            return PanelAnswer(
                provider_id=reply.provider_id,
                model=reply.model,
                label=label,
                family=model_family(reply.model),
                text=reply.text,
                latency_ms=latency_ms,
                cached=bool(getattr(reply, "cached", False)),
            )
        except Exception as exc:  # noqa: BLE001 - one opinion failing must not abort the panel
            latency_ms = round((time.monotonic() - started) * 1000)
            return PanelAnswer(
                provider_id=provider_id,
                model=model_name,
                label=f"{provider_id}/{model_name}",
                family=target_family(target),
                text=None,
                latency_ms=latency_ms,
                error=f"{type(exc).__name__}: {exc}",
            )

    with _cf.ThreadPoolExecutor(max_workers=min(_WORKERS, len(picks))) as ex:
        answers = tuple(ex.map(ask_one, picks))

    synthesis = _synthesize(pool, prompt, msgs, answers, token_limit, timeout) if synthesize else None
    return PanelResult(
        prompt=prompt,
        answers=answers,
        requested_count=requested_count,
        selected_count=len(picks),
        max_tokens=token_limit,
        truncated=truncated or len(picks) < selected_count,
        synthesis=synthesis,
    )


def render_panel_markdown(result: PanelResult, *, title: str = "freellmpool panel") -> str:
    prompt = result.prompt.replace("\n", " ").strip()
    if len(prompt) > 70:
        prompt = prompt[:69] + "..."
    lines = [f'{title} - {len(result.answers)} free models on: "{prompt}"', ""]
    for answer in result.answers:
        if answer.error:
            lines.append(f"### {answer.label}  (failed)\n{answer.error}\n")
        else:
            tag = "cache" if answer.cached else f"{answer.latency_ms}ms"
            lines.append(f"### {answer.label}  ({tag})\n{answer.text or ''}\n")
    if result.synthesis is not None:
        if result.synthesis.error:
            lines.append(f"### synthesis (failed)\n{result.synthesis.error}")
        else:
            label = f"{result.synthesis.provider_id}/{result.synthesis.model}"
            lines.append(f"### synthesis - via {label}\n{result.synthesis.text or ''}")
    return "\n".join(lines).rstrip()


def target_family(target: Any) -> str | None:
    return model_family(getattr(target, "model", ""))


def model_family(name: str) -> str | None:
    """Return a coarse model family label for panel diversity decisions."""
    normalized = normalize_model_name(name)
    match = re.match(r"[a-z]+", normalized)
    family = match.group(0) if match else ""
    return family if len(family) >= 4 else None


def _synthesize(
    pool: Pool,
    prompt: str,
    messages: Sequence[dict[str, str]],
    answers: Sequence[PanelAnswer],
    max_tokens: int,
    timeout: float,
) -> PanelSynthesis | None:
    successful = [answer for answer in answers if answer.ok]
    if not successful:
        return None
    blob = "\n\n".join(f"[{answer.label}]\n{answer.text}" for answer in successful)
    syn_prompt = (
        "Below are several models' answers to the same question. Synthesize the single "
        "best, correct, concise answer, resolving any disagreements.\n\n"
        f"Question: {prompt}\n\n{blob}"
    )
    try:
        reply = pool.chat(
            messages_from_prompt(syn_prompt),
            routing="quality",
            max_tokens=max(max_tokens, 1024),
            timeout=timeout,
        )
        return PanelSynthesis(
            provider_id=reply.provider_id,
            model=reply.model,
            text=reply.text,
        )
    except Exception as exc:  # noqa: BLE001 - synthesis is a bonus, never fatal
        return PanelSynthesis(
            provider_id=None,
            model=None,
            text=None,
            error=f"{type(exc).__name__}: {exc}",
        )


def _clamp_int(value: object, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _int_or_default(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
