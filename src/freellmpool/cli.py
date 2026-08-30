"""Command-line interface for freellmpool.

freellmpool ask "question"        one-shot completion (reads stdin too)
freellmpool tokenmax "question"   🌈 fan out to eligible targets, synthesize the swarm
freellmpool providers            list configured / available providers
freellmpool quota                show today's per-provider usage
freellmpool proxy                run the OpenAI-compatible proxy server
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

from . import __version__
from .config import (
    configured_providers,
    effective_env,
    load_catalog,
    resolve_alias,
    settings,
    split_provider_model,
)
from .conformance import (
    FEATURE_ANTHROPIC_MESSAGES,
    FEATURE_CHAT,
    FEATURE_JSON,
    FEATURE_JSON_SCHEMA,
    FEATURE_RESPONSES,
    FEATURE_STREAMING,
    FEATURE_TOOLS,
    FEATURE_VISION,
    FEATURES,
    ConformanceStore,
    run_target_canaries,
)
from .errors import AllProvidersExhausted, NoProvidersConfigured
from .mode import (
    WISE_DEFAULT_MAX_TOKENS,
    WISE_DEFAULT_ROUTING,
    WISE_EXPENSIVE_MODEL_THRESHOLD,
    WISE_TOKENMAX_DEFAULT_MODELS,
    confirm_expensive_operation,
    declared_quota_exhausted,
    declared_targets,
    is_wise_enabled,
    render_quota_wise_status,
    targets_with_declared_headroom,
)
from .panel import render_panel_markdown, run_panel
from .quota import QuotaStore
from .roles import format_roles, get_role
from .router import Pool
from .routing_modes import PUBLIC_ROUTING_ALIASES, routing_override
from .savings import format_saved
from .task_quality import TASK_HINTS


def _read_stdin() -> str:
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    return sys.stdin.read()


def _runtime_catalog():
    """Built-in plus registered/entry-point providers, merged by provider id."""

    from .plugins import registered_providers

    by_id = {provider.id: provider for provider in load_catalog()}
    for provider in registered_providers():
        by_id[provider.id] = provider
    return list(by_id.values())


def cmd_ask(args: argparse.Namespace) -> int:
    stdin = _read_stdin()
    prompt = args.prompt or ""
    if stdin:
        prompt = f"{stdin}\n\n{prompt}".strip() if prompt else stdin

    if not prompt.strip():
        print("freellmpool: no prompt provided (pass text or pipe stdin)", file=sys.stderr)
        return 3

    role = get_role(args.role) if args.role else None
    if args.role and role is None:
        print(
            f"freellmpool: unknown role '{args.role}'\n",
            file=sys.stderr,
        )
        print(format_roles(), file=sys.stderr)
        return 2

    # Support `--model provider/model` as a shorthand for picking an exact
    # model on an exact provider (in addition to `--providers` + bare `--model`).
    # Common OpenAI/Anthropic names (gpt-4o-mini, ...) resolve to a free target.
    model_filter = resolve_alias(args.model) if args.model else None
    if model_filter == "auto":
        model_filter = None
    provider_filter = args.providers.split(",") if args.providers else None
    if model_filter and "/" in model_filter:
        # Only treat the prefix as a provider when it's a real provider id; otherwise keep the
        # full slash-containing model name (e.g. huggingface/Qwen ids, openrouter :free ids).
        prov, mdl = split_provider_model(model_filter, {p.id for p in configured_providers()})
        if prov is not None:
            provider_filter, model_filter = prov, mdl

    system = args.system
    if system is None and role is not None and role.system_prefix is not None:
        system = role.system_prefix
    if args.json:
        json_rule = "Respond with a single valid JSON value and nothing else — no prose, no markdown fences."
        system = f"{system}\n{json_rule}" if system else json_rule

    pool = Pool.from_default_config()
    pool_env = getattr(pool, "env", os.environ)
    mode_settings = settings(pool_env)
    has_routing_config = bool(pool_env.get("FREELLMPOOL_ROUTING") or mode_settings.get("routing"))
    wise = is_wise_enabled(pool_env, override=args.mode, settings=mode_settings)
    max_tokens = args.max_tokens
    if max_tokens is None:
        max_tokens = (
            role.max_tokens
            if (role is not None and role.max_tokens is not None)
            else (WISE_DEFAULT_MAX_TOKENS if wise else 1024)
        )

    temperature = args.temperature
    if temperature is None:
        temperature = role.temperature if (role is not None and role.temperature is not None) else 0.0

    routing = routing_override(args.routing) if args.routing is not None else None
    if args.routing is None and routing is None and role is not None and role.routing is not None:
        routing = role.routing
    if args.routing is None and routing is None and role is None and wise:
        routing = WISE_DEFAULT_ROUTING
    if args.routing is None and routing is None and role is None and args.mode == "normal":
        if not has_routing_config:
            routing = "fair"
    task = args.task if args.task is not None else (role.task if role is not None else None)

    second_opinion = bool(args.second_opinion or (role is not None and role.name == "second-opinion"))
    if second_opinion:
        if args.json:
            print("freellmpool: --json is not supported with --second-opinion", file=sys.stderr)
            return 2
        result = run_panel(
            pool,
            prompt=prompt,
            system=system,
            n=args.opinions,
            routing=routing or "quality",
            model=model_filter,
            providers=provider_filter,
            max_tokens=max_tokens,
            timeout=args.timeout,
            synthesize=args.synthesize,
            task=task,
        )
        if not result.answers:
            print("freellmpool: no providers configured", file=sys.stderr)
            return 3
        print(render_panel_markdown(result, title="freellmpool second opinion panel"))
        return 0 if result.successful_answers else 4

    if wise and not args.model and not args.providers:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        targets = pool.rank_targets(
            messages,
            routing=routing,
            model=model_filter,
            providers=provider_filter,
            task=task,
        )
        snapshot = pool.quota.snapshot()
        if declared_quota_exhausted(targets, snapshot):
            print(
                "freellmpool: declared local free quota is exhausted in wise mode; "
                "rerun with an explicit --model or --providers if you want to override.",
                file=sys.stderr,
            )
            return 4
        headroom_targets = targets_with_declared_headroom(targets, snapshot)
        if headroom_targets:
            target = headroom_targets[0]
            provider_filter = [target.provider.id]
            model_filter = target.model
    try:
        reply = pool.ask(
            prompt,
            system=system,
            model=model_filter,
            providers=provider_filter,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=args.timeout,
            routing=routing,
            task=task,
        )
    except NoProvidersConfigured as exc:
        print(f"freellmpool: {exc}", file=sys.stderr)
        return 3
    except AllProvidersExhausted as exc:
        print(f"freellmpool: {exc}", file=sys.stderr)
        return 4

    text = reply.text
    if args.json:
        text = _strip_fences(text)
    print(text)
    if args.verbose:
        saved = format_saved(reply.prompt_tokens, reply.completion_tokens)
        print(
            f"\n[served by {reply.provider_id}/{reply.model} · {saved}]",
            file=sys.stderr,
        )
    return 0


def cmd_roles(args: argparse.Namespace) -> int:
    print(format_roles())
    return 0


def cmd_tokenmax(args: argparse.Namespace) -> int:
    """🌈 Fan out to automatically eligible ranked targets across configured providers.

    The hard cap is 256; ``--max-models``, routing, and wise mode can narrow the
    swarm. By default its returned answers are synthesized into one answer. The
    genuine rainbow animation runs on a real terminal.
    """
    from .tokenmax import HARD_CAP, RAINBOW_BANNER, RainbowThrob, fan_out, select_targets

    stdin = _read_stdin()
    prompt = args.prompt or ""
    if stdin:
        prompt = f"{stdin}\n\n{prompt}".strip() if prompt else stdin
    if not prompt.strip():
        print("freellmpool: no prompt provided (pass text or pipe stdin)", file=sys.stderr)
        return 3

    pool = Pool.from_default_config()
    if not pool.providers:
        print(
            "freellmpool: no providers configured; set at least one API key "
            "(see .env.example) before tokenmaxxing.",
            file=sys.stderr,
        )
        return 3

    msgs: list[dict[str, str]] = []
    if args.system:
        msgs.append({"role": "system", "content": args.system})
    msgs.append({"role": "user", "content": prompt})

    pool_env = getattr(pool, "env", os.environ)
    mode_settings = settings(pool_env)
    has_routing_config = bool(pool_env.get("FREELLMPOOL_ROUTING") or mode_settings.get("routing"))
    wise = is_wise_enabled(pool_env, override=args.mode, settings=mode_settings)
    routing_for_mode = None
    if not has_routing_config:
        if wise:
            routing_for_mode = WISE_DEFAULT_ROUTING
        elif args.mode == "normal":
            routing_for_mode = "fair"
    wise_has_declared_targets = False
    if wise:
        if routing_for_mode is None:
            candidates, _n_providers = select_targets(pool, msgs, None)
        else:
            candidates, _n_providers = select_targets(pool, msgs, None, routing=routing_for_mode)
        wise_has_declared_targets = bool(declared_targets(candidates))
        snapshot = pool.quota.snapshot()
        if declared_quota_exhausted(candidates, snapshot):
            print(
                "freellmpool: declared local free quota is exhausted in wise mode; "
                "tokenmax will not fan out to unknown or paid fallback targets implicitly.",
                file=sys.stderr,
            )
            return 4
        headroom_targets = targets_with_declared_headroom(candidates, snapshot)
        if headroom_targets:
            candidates = headroom_targets
        max_models = args.max_models
        if max_models is None:
            max_models = WISE_TOKENMAX_DEFAULT_MODELS
        limit = max(1, min(HARD_CAP, int(max_models)))
        picks = candidates[:limit]
        n_providers = len({target.provider.id for target in picks})
    else:
        if routing_for_mode is None:
            picks, n_providers = select_targets(pool, msgs, args.max_models)
        else:
            picks, n_providers = select_targets(pool, msgs, args.max_models, routing=routing_for_mode)
    if not picks:
        print("freellmpool: no models available to blast", file=sys.stderr)
        return 3
    if wise:
        if len(picks) > WISE_EXPENSIVE_MODEL_THRESHOLD:
            label = f"tokenmax fan-out to {len(picks)} models across {n_providers} providers"
            if not confirm_expensive_operation(label, assume_yes=args.yes):
                return 4

    label = f"TOKENMAXXING {len(picks)} models across {n_providers} providers"
    with RainbowThrob(label):
        answered, failed = fan_out(
            pool,
            msgs,
            picks,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
        )

    print(
        f"{RAINBOW_BANNER} TOKENMAX — {len(picks)} models / {n_providers} providers · "
        f"{len(answered)} answered, {len(failed)} unavailable {RAINBOW_BANNER}\n"
    )
    for lbl, text in answered:
        print(f"### {lbl}\n{text}\n")
    if failed:
        shown = ", ".join(failed[:30]) + ("…" if len(failed) > 30 else "")
        print(f"({len(failed)} unavailable: {shown})\n", file=sys.stderr)

    if not args.no_synthesize and answered:
        blob = "\n\n".join(f"[{lbl}]\n{txt}" for lbl, txt in answered)
        syn_prompt = (
            "Below are many models' answers to the same question. Synthesize the single "
            "best, correct, concise answer, weighing agreement and discarding outliers.\n\n"
            f"Question: {prompt}\n\n{blob}"
        )
        try:
            syn_model = None
            syn_providers = None
            if wise:
                syn_messages = [{"role": "user", "content": syn_prompt}]
                syn_targets = pool.rank_targets(syn_messages, routing="quality")
                syn_headroom = targets_with_declared_headroom(syn_targets, pool.quota.snapshot())
                if syn_headroom:
                    syn_model = syn_headroom[0].model
                    syn_providers = [syn_headroom[0].provider.id]
                elif wise_has_declared_targets:
                    print(
                        "(synthesis skipped: declared local free quota exhausted in wise mode)",
                        file=sys.stderr,
                    )
                    return 0
            syn = pool.chat(
                [{"role": "user", "content": syn_prompt}],
                model=syn_model,
                providers=syn_providers,
                routing="quality",
                max_tokens=1024,
                timeout=args.timeout,
            )
            print(f"{RAINBOW_BANNER} SYNTHESIS — via {syn.provider_id}/{syn.model}\n{syn.text}")
        except Exception as exc:  # noqa: BLE001 — synthesis is a bonus, never fatal
            print(f"(synthesis failed: {type(exc).__name__}: {exc})", file=sys.stderr)
    return 0


def _strip_fences(text: str) -> str:
    """Remove a leading ```json / ``` fence and trailing ``` if present."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def cmd_providers(args: argparse.Namespace) -> int:
    catalog = _runtime_catalog()
    configured = {p.id for p in configured_providers(catalog)}
    n_models = sum(1 for p in catalog for m in p.models if m.enabled)
    print(f"freellmpool catalog: {len(catalog)} providers, {n_models} models\n")
    for p in catalog:
        mark = "✓" if p.id in configured else "·"
        status = "configured" if p.id in configured else f"set {p.key_env}"
        on = sum(1 for m in p.models if m.enabled)
        off = len(p.models) - on
        count = f"{on} models" + (f" (+{off} off)" if off else "")
        print(f"  {mark} {p.id:<12} {p.label:<28} {count:<16} [{status}]")
    if not configured:
        print("\nNo providers configured yet. See .env.example for the env vars to set.")
    return 0


def cmd_providers_health(args: argparse.Namespace) -> int:
    from .healthcheck import render_health_table, run_healthcheck

    pool = Pool.from_default_config()
    provider_filter = args.providers.split(",") if args.providers else None
    rows = run_healthcheck(
        pool,
        model=args.model,
        providers=provider_filter,
        timeout=args.timeout,
    )
    print(render_health_table(rows))
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    import json

    catalog = _runtime_catalog()
    configured = {p.id for p in configured_providers(catalog)}
    conformance = ConformanceStore()
    conformance_snapshot = conformance.snapshot()
    only = set(args.providers.split(",")) if args.providers else None
    if args.json:
        rows = []
        for provider in catalog:
            if (only is not None and provider.id not in only) or (
                args.configured_only and provider.id not in configured
            ):
                continue
            for model in provider.models:
                if not model.enabled and not args.all:
                    continue
                evidence = conformance.evidence(
                    provider,
                    model.name,
                    snapshot=conformance_snapshot,
                )
                rows.append(
                    {
                        "provider": provider.id,
                        "model": model.name,
                        "enabled": model.enabled,
                        "configured": provider.id in configured,
                        "capabilities": evidence,
                        "verified_features": sorted(
                            feature
                            for feature, result in evidence.items()
                            if result.get("status") == "pass"
                        ),
                    }
                )
        print(json.dumps(rows, separators=(",", ":")))
        return 0

    shown = 0
    for p in catalog:
        if only is not None and p.id not in only:
            continue
        if args.configured_only and p.id not in configured:
            continue
        mark = "✓" if p.id in configured else "·"
        keyless = "  (keyless)" if p.keyless and p.id in configured else ""
        print(f"\n{mark} {p.id}  —  {p.label}{keyless}")
        for m in p.models:
            if not m.enabled and not args.all:
                continue
            shown += 1
            tag = "  (off by default)" if not m.enabled else ""
            print(f"    {p.id}/{m.name}{tag}")
    if shown == 0:
        print("No models match. Try `freellmpool providers` to see configuration status.")
        return 0
    print(
        f"\nPass any id above to `--model`, e.g. "
        f'`freellmpool ask -m {catalog[0].id}/{catalog[0].models[0].name} "hi"`,'
    )
    print("or just `--model <model-name>` to use that model on any provider that has it.")
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    from .benchmark import benchmark, render_table

    pool = Pool.from_default_config()
    if not pool.providers:
        print(
            "freellmpool: no providers configured; set at least one API key "
            "(see .env.example) before benchmarking.",
            file=sys.stderr,
        )
        return 3
    provider_filter = args.providers.split(",") if args.providers else None
    print(
        f"Benchmarking {len(pool.providers)} providers "
        f"(one model each{', pinned' if args.model else ''})...",
        file=sys.stderr,
    )
    rows = benchmark(
        pool,
        model=args.model,
        providers=provider_filter,
        timeout=args.timeout,
    )
    print(render_table(rows))
    return 0


def cmd_quota(args: argparse.Namespace) -> int:
    store = QuotaStore()
    snap = store.snapshot()
    if not snap:
        print("No usage recorded today (UTC).")
        return 0
    print("Today's usage (UTC):")
    for key, count in sorted(snap.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>6}  {key}")
    return 0


def cmd_quota_wise_status(args: argparse.Namespace) -> int:
    pool = Pool.from_default_config()
    mode_settings = settings(pool.env)
    print(
        render_quota_wise_status(
            pool.providers,
            pool.quota.snapshot(),
            active=is_wise_enabled(pool.env, settings=mode_settings),
        )
    )
    return 0


def _quota_leaderboard(limit: int = 5) -> list[tuple[str, float]]:
    """Top providers by requests served today, as (id, fraction-of-leader)."""
    totals: dict[str, int] = {}
    for key, count in QuotaStore().snapshot().items():
        pid = key.split("::", 1)[0]
        totals[pid] = totals.get(pid, 0) + int(count)
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:limit]
    top = ranked[0][1] if ranked and ranked[0][1] > 0 else 1
    return [(pid, count / top) for pid, count in ranked if count > 0]


def cmd_stats(args: argparse.Namespace) -> int:
    from .stats import StatsStore

    snap = StatsStore().snapshot()
    tokens = snap["prompt_tokens"] + snap["completion_tokens"]
    print("freellmpool — lifetime (served free):")
    print(f"  requests:    {snap['requests']:,}")
    print(
        f"  tokens:      {tokens:,}  "
        f"({snap['prompt_tokens']:,} in / {snap['completion_tokens']:,} out)"
    )
    print(f"  cache hits:  {snap['cache_hits']:,}")
    print(f"  {format_saved(snap['prompt_tokens'], snap['completion_tokens'])}")
    if snap.get("first_seen"):
        print(f"  since:       {snap['first_seen']}")
    return 0


def cmd_badge(args: argparse.Namespace) -> int:
    from . import svg
    from .stats import StatsStore

    snap = StatsStore().snapshot()
    if args.summary:
        out = svg.summary_svg(snap, _quota_leaderboard())
    else:
        out = svg.badge_svg(snap, metric=args.metric)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(out)
    return 0


def _format_capacity_row(row) -> str:
    quota = "?" if row.quota_hint <= 0 else str(row.quota_hint)
    key = "keyless" if row.keyless else (row.key_env or "-")
    expiry = f" expires={row.expires_at}" if row.expires_at else ""
    return (
        f"  {row.status:<11} {row.provider_id:<13} {row.label:<28} "
        f"used={row.used_today}/{quota:<5} models={row.enabled_models:<3} "
        f"key={key}{expiry}  {row.reason}"
    )


def cmd_keys_status(args: argparse.Namespace) -> int:
    from .capacity import build_capacity_report
    from .key_inventory import default_inventory_path, load_inventory

    inventory_path = default_inventory_path()
    inventory = load_inventory(inventory_path)
    report = build_capacity_report(target=args.target, inventory=inventory)
    print(f"Key inventory: {inventory_path}")
    print(f"Records: {len(inventory)}")
    print(f"Healthy providers: {report.healthy_count}/{args.target}\n")
    for row in report.providers:
        if args.all or row.status != "missing":
            print(_format_capacity_row(row))
    return 0


def cmd_keys_checklist(args: argparse.Namespace) -> int:
    from .capacity import build_capacity_report
    from .key_inventory import load_inventory

    report = build_capacity_report(target=args.target, inventory=load_inventory())
    todo = report.checklist()
    if not todo:
        print(f"Enough healthy providers: {report.healthy_count}/{args.target}.")
        return 0
    print(f"Manual key checklist to reach {args.target} healthy providers:")
    for row in todo:
        print(f"  - {row.provider_id}: create a key manually, then set {row.key_env}")
    return 0


def _choose_provider(catalog, provider_id: str | None):
    providers = [p for p in catalog if p.key_env]
    if provider_id:
        needle = provider_id.lower()
        matches = [p for p in providers if p.id.lower() == needle or p.label.lower() == needle]
        if not matches:
            raise SystemExit(
                f"provider is not configured in local providers.toml, or is keyless/external-only: {provider_id}"
            )
        return matches[0]

    print("Choose a provider to configure:")
    for i, provider in enumerate(providers, start=1):
        print(f"  {i}. {provider.id} ({provider.key_env})")

    while True:
        raw = input("Provider number or id: ").strip()
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(providers):
                return providers[idx - 1]

        for provider in providers:
            if provider.id == raw:
                return provider

        print("Invalid provider, try again.")


def _yes(raw: str) -> bool:
    return raw.strip().lower() in {"y", "yes"}


def _load_or_sync_external_catalog():
    from .catalog import load_external_catalog, sync_external_catalog

    external = load_external_catalog()
    if external:
        return external
    try:
        _, external = sync_external_catalog()
    except Exception:  # noqa: BLE001 - keys add can continue with manual creation
        return []
    return external


def _import_or_create_provider(provider_name: str, args: argparse.Namespace) -> str | None:
    from .catalog import (
        create_user_provider_stub,
        discover_openai_models,
        import_external_provider_to_user_catalog,
        suggest_external_provider,
    )

    external = _load_or_sync_external_catalog()
    suggestion = suggest_external_provider(provider_name, external)
    if suggestion:
        provider_slug = suggestion.provider.slug.replace("-", "_")
        query_slug = provider_name.lower().replace("_", "-")
        is_exact_provider = suggestion.exact and query_slug in {
            suggestion.provider.slug,
            provider_slug,
        }
        if is_exact_provider or (
            not args.yes
            and _yes(
                input(
                    f"Provider not found. Use external match "
                    f"{suggestion.provider.name} (matched {suggestion.matched})? [y/N] "
                )
            )
        ):
            local_id = import_external_provider_to_user_catalog(suggestion.provider.name)
            print(
                f"Imported external provider '{suggestion.provider.name}' as local provider '{local_id}'."
            )
            return local_id

    if args.yes and not args.base_url:
        print(
            "Provider not found. Pass --base-url to create it non-interactively.", file=sys.stderr
        )
        return None

    if not args.yes and not _yes(
        input(f"Provider '{provider_name}' not found. Create it manually? [y/N] ")
    ):
        return None

    base_url = args.base_url or input("OpenAI-compatible API base URL: ").strip()
    model = args.model or (
        "" if args.yes else input("Default model id (blank to autodiscover): ").strip()
    )
    if not model:
        api_key = getattr(args, "value", None)
        if not api_key and not args.yes:
            import getpass

            api_key = getpass.getpass("API key for model discovery (blank if not needed): ").strip()
            if api_key:
                args.value = api_key
        try:
            models = discover_openai_models(base_url, api_key=api_key or None)
        except ValueError as exc:
            print(f"Could not autodiscover models: {exc}", file=sys.stderr)
            models = []
        model = _choose_discovered_model(models, args)
        if not model and not args.yes:
            model = input("Default model id: ").strip()
    try:
        local_id = create_user_provider_stub(name=provider_name, base_url=base_url, model=model)
    except ValueError as exc:
        print(f"Could not create provider: {exc}", file=sys.stderr)
        return None
    print(f"Created local provider '{local_id}' in user providers.toml.")
    return local_id


def _choose_discovered_model(models: list[str], args: argparse.Namespace) -> str | None:
    if not models:
        return None
    if len(models) == 1 or args.yes:
        print(f"Discovered model: {models[0]}")
        return models[0]
    print("Discovered models:")
    for i, model in enumerate(models[:10], start=1):
        print(f"  {i}. {model}")
    raw = input("Model number or id: ").strip()
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= min(len(models), 10):
            return models[idx - 1]
    if raw in models:
        return raw
    return None


def cmd_keys_add(args: argparse.Namespace) -> int:
    import getpass
    from datetime import date

    from .config import load_catalog, load_config_file
    from .key_inventory import (
        KeyRecord,
        append_inventory_record,
        default_config_path,
        upsert_config_key,
    )

    if getattr(args, "provider_arg", None) and not args.provider:
        args.provider = args.provider_arg
    try:
        catalog = load_catalog()
    except FileNotFoundError:
        catalog = []
    try:
        provider = _choose_provider(catalog, args.provider)
    except SystemExit:
        if not args.provider:
            raise
        local_id = _import_or_create_provider(args.provider, args)
        if not local_id:
            return 3
        provider = _choose_provider(load_catalog(), local_id)

    value = getattr(args, "value", None)
    if not value:
        value = getpass.getpass(f"Paste {provider.key_env}: ").strip()

    if not value:
        print("No value provided.", file=sys.stderr)
        return 3

    existing_keys = load_config_file().get("keys", {})
    known_env = {str(k): str(v) for k, v in existing_keys.items() if v}
    known_env.update(os.environ)
    extra_values: dict[str, str] = {}
    for env_var in provider.extra_env:
        if known_env.get(env_var):
            continue
        extra_value = input(f"Paste {env_var}: ").strip()
        if not extra_value:
            print(f"No value provided for {env_var}.", file=sys.stderr)
            return 3
        extra_values[env_var] = extra_value

    if not args.yes:
        names = [str(provider.key_env), *extra_values]
        answer = input(f"Write {', '.join(names)} to {default_config_path()}? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("Cancelled.")
            return 1

    config_path = upsert_config_key(provider.key_env, value)
    for env_var, extra_value in extra_values.items():
        config_path = upsert_config_key(env_var, extra_value)

    written_names = [str(provider.key_env), *extra_values]
    inventory_path = append_inventory_record(
        KeyRecord(
            provider=provider.id,
            env_var=provider.key_env,
            label=args.label or "manual",
            created_at=date.today().isoformat(),
            commercial_allowed=args.commercial_allowed,
            notes=args.notes or "added with freellmpool keys add",
        )
    )

    print(f"Added {provider.id} key metadata.")
    print(f"Wrote: {', '.join(written_names)}")
    print(f"Config: {config_path}")
    print(f"Inventory: {inventory_path}")
    unlocked = sum(1 for model in provider.models if model.enabled)
    suffix = "route" if unlocked == 1 else "routes"
    print(f"Unlocked {unlocked} enabled model {suffix} for {provider.label}.")
    print("Next command:")
    print("  freellmpool providers health -p " + provider.id)
    return 0


def cmd_capacity_status(args: argparse.Namespace) -> int:
    from .capacity import build_capacity_report
    from .catalog import load_external_catalog, match_local_provider, sync_external_catalog
    from .key_inventory import load_inventory

    external = []
    cache_note = None
    if args.refresh:
        try:
            path, external = sync_external_catalog(timeout=args.catalog_timeout)
            cache_note = f"External catalog refreshed: {len(external)} providers ({path})"
        except Exception as exc:  # noqa: BLE001 - capacity must still work offline
            external = load_external_catalog()
            cache_note = f"External catalog refresh failed ({type(exc).__name__}); using cache with {len(external)} providers"
    else:
        external = load_external_catalog()
        cache_note = f"External catalog cache: {len(external)} providers"

    local_catalog = load_catalog()
    linked = {match_local_provider(item, local_catalog) for item in external}
    linked.discard(None)
    external_only = [item for item in external if match_local_provider(item, local_catalog) is None]

    report = build_capacity_report(
        target=args.target, inventory=load_inventory(), catalog=local_catalog
    )
    print(f"LLM capacity: {report.healthy_count}/{args.target} healthy providers")
    print(cache_note)
    if external:
        print(f"Catalog links: {len(linked)} linked locally, {len(external_only)} external-only")
    if report.low_quota_count:
        print(f"Warning: {report.low_quota_count} provider(s) are near quota.")
    if report.needs_action:
        print(f"Action recommended: add {args.target - report.healthy_count} provider(s).")
    print()
    for row in report.providers:
        if args.all or row.status in {"healthy", "low_quota", "exhausted", "invalid_key"}:
            print(_format_capacity_row(row))
    if args.all and external_only:
        print()
        print("External-only catalog candidates, not in local providers.toml:")
        for item in external_only[: args.external_limit]:
            score = item.best_tpd or item.best_rpd or item.best_rpm
            print(
                f"  external    {item.name:<24} score={score:<8} models={item.model_count:<3} link={item.url or '-'}"
            )
    return 0


def cmd_catalog_sync(args: argparse.Namespace) -> int:
    from .catalog import sync_external_catalog

    path, providers = sync_external_catalog(timeout=args.timeout)
    print(f"Synced {len(providers)} external providers.")
    print(f"Cache: {path}")
    top = providers[:5]
    if top:
        print("Most generous external entries:")
        for provider in top:
            score = provider.best_tpd or provider.best_rpd or provider.best_rpm
            print(f"  {provider.name}: score={score} models={provider.model_count}")
    return 0


def cmd_local_discover(args: argparse.Namespace) -> int:
    """Preview explicitly requested or fixed-list loopback model runtimes."""

    import json

    from .local_runtime import discover_known_runtimes, discover_runtime

    if args.name or args.base_url:
        if not args.name:
            print("freellmpool: --base-url requires --name", file=sys.stderr)
            return 2
        try:
            runtime = discover_runtime(
                name=args.name,
                base_url=args.base_url,
                timeout=args.timeout,
            )
        except ValueError as exc:
            print(f"freellmpool: {exc}", file=sys.stderr)
            return 3
        if args.json:
            print(json.dumps(runtime.as_json(), separators=(",", ":"), sort_keys=True))
        else:
            print(f"Found {runtime.label} at {runtime.base_url}")
            print(f"  pin-only provider id: {runtime.provider_id}")
            print(f"  models ({len(runtime.models)}):")
            for model in runtime.models:
                print(f"    {model}")
            print("Preview only; no files were written.")
        return 0

    found, unavailable = discover_known_runtimes(timeout=args.timeout)
    if args.json:
        print(
            json.dumps(
                {
                    "found": [runtime.as_json() for runtime in found],
                    "unavailable": unavailable,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    else:
        if not found:
            print("No known local OpenAI-compatible runtime answered on loopback.")
        for runtime in found:
            print(f"Found {runtime.label}: {runtime.base_url} ({len(runtime.models)} models)")
        print("Preview only; no files were written.")
    return 0


def cmd_local_import(args: argparse.Namespace) -> int:
    """Discover and atomically import one explicitly selected local runtime."""

    from .local_runtime import discover_runtime, import_runtime

    if not args.yes:
        print(
            "freellmpool: local import changes the user catalog; review `local discover` "
            "then repeat with --yes",
            file=sys.stderr,
        )
        return 2
    try:
        runtime = discover_runtime(
            name=args.name,
            base_url=args.base_url,
            timeout=args.timeout,
        )
        if not runtime.models:
            raise ValueError("local runtime exposed no models; nothing to import")
        path = import_runtime(runtime)
    except ValueError as exc:
        print(f"freellmpool: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(
            f"freellmpool: local catalog update failed ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 3
    print(f"Imported {runtime.provider_id} as a pin-only local provider.")
    print(f"Catalog: {path}")
    print("No credential was read or stored; automatic routing remains unchanged.")
    print("Example exact pin:")
    print(
        "  "
        + shlex.join(
            [
                "freellmpool",
                "ask",
                "--providers",
                runtime.provider_id,
                "--model",
                runtime.models[0],
                "hello",
            ]
        )
    )
    print("Revert:")
    print(f"  freellmpool local remove {runtime.provider_id} --yes")
    return 0


def cmd_local_remove(args: argparse.Namespace) -> int:
    """Remove only a user-catalog block managed by ``local import``."""

    from .local_runtime import remove_runtime

    if not args.yes:
        print("freellmpool: local remove requires --yes", file=sys.stderr)
        return 2
    try:
        removed = remove_runtime(args.provider_id)
    except ValueError as exc:
        print(f"freellmpool: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(
            f"freellmpool: local catalog update failed ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 3
    if not removed:
        print(f"freellmpool: no managed local runtime named {args.provider_id}", file=sys.stderr)
        return 3
    print(f"Removed {args.provider_id} from the user provider catalog.")
    return 0


def cmd_catalog_status(args: argparse.Namespace) -> int:
    from .catalog import default_external_catalog_path, load_external_catalog

    path = default_external_catalog_path()
    providers = load_external_catalog(path)
    if not providers:
        print(f"No external catalog cache found at {path}.")
        print("Run: freellmpool catalog sync")
        return 1
    print(f"External catalog: {len(providers)} providers")
    print(f"Cache: {path}")
    for provider in providers[: args.limit]:
        score = provider.best_tpd or provider.best_rpd or provider.best_rpm
        print(
            f"  {provider.name:<28} score={score:<8} models={provider.model_count:<3} base={provider.base_url or '-'}"
        )
    return 0


def cmd_capability_sync(args: argparse.Namespace) -> int:
    from .capability import sync_capability_table

    aa_key = os.environ.get("FREELLMPOOL_AA_API_KEY")
    path, stats = sync_capability_table(timeout=args.timeout, aa_api_key=aa_key)
    by_source = ", ".join(f"{k}={v}" for k, v in sorted(stats["by_source"].items()))
    print(f"Synced capability scores → {path}")
    print(
        f"  benchmark models fetched: arena={stats['arena']}  "
        f"aider={stats['aider']}  aa={stats['aa']}"
    )
    print(f"  catalog models mapped: {stats['mapped']} ({by_source or 'none'})")
    if not aa_key:
        print(
            "  Artificial Analysis skipped — set FREELLMPOOL_AA_API_KEY for much broader "
            "coverage (its Intelligence Index covers most current/open models and wins)."
        )
    print("  Models not covered by a benchmark fall back to a name heuristic at runtime.")
    # Source attribution (required for Artificial Analysis; courtesy for the rest).
    sources = ["LMArena (https://lmarena.ai/)", "Aider (https://aider.chat/)"]
    if stats["aa"]:
        sources.append("Artificial Analysis (https://artificialanalysis.ai/)")
    print("  Scores via " + ", ".join(sources) + ".")
    return 0


def cmd_capability_status(args: argparse.Namespace) -> int:
    from .capability import capability_table, model_capability, user_capability_path
    from .config import load_catalog

    table = capability_table()
    user = user_capability_path()
    print(f"Capability scores: {len(table)} benchmark-scored models")
    print(f"  user cache: {user if user.exists() else '(none — using bundled snapshot)'}")
    names = sorted({m.name for p in load_catalog() for m in p.models})
    scored = sorted(((model_capability(n, table), n) for n in names), reverse=True)
    covered = sum(1 for n in names if _in_table(n, table))
    print(f"  catalog models with a benchmark score: {covered}/{len(names)} (rest use heuristic)")
    print(f"  top {args.limit} catalog models by capability:")
    for cap, name in scored[: args.limit]:
        print(f"    {cap:.3f}  {name}")
    return 0


def cmd_conformance_run(args: argparse.Namespace) -> int:
    """Run a bounded deterministic feature matrix and persist sanitized evidence."""

    import json

    features = tuple(part.strip() for part in args.features.split(",") if part.strip())
    unknown = sorted(set(features) - set(FEATURES))
    if not features or unknown or len(set(features)) != len(features):
        detail = f": {', '.join(unknown)}" if unknown else ""
        print(f"freellmpool: invalid conformance feature selection{detail}", file=sys.stderr)
        return 2
    catalog = _runtime_catalog()
    try:
        env = _conformance_env(catalog)
    except ValueError:
        print(
            "freellmpool: invalid bounded conformance key map",
            file=sys.stderr,
        )
        return 2
    configured = configured_providers(catalog, env)
    targets = []
    if args.include_disabled:
        if not args.provider or not args.model or args.providers or args.all_models:
            print(
                "freellmpool: --include-disabled requires one exact --provider and --model",
                file=sys.stderr,
            )
            return 2
        provider = next((item for item in configured if item.id == args.provider), None)
        model = (
            next((item for item in provider.models if item.name == args.model), None)
            if provider is not None
            else None
        )
        if model is None:
            print(
                "freellmpool: exact disabled canary target is missing or not configured",
                file=sys.stderr,
            )
            return 3
        if model.enabled:
            print(
                "freellmpool: --include-disabled is only for a model that is off by default",
                file=sys.stderr,
            )
            return 2
        targets = [(provider, model.name)]
    else:
        if args.provider:
            print(
                "freellmpool: --provider is reserved for exact --include-disabled canaries; "
                "use --providers for enabled targets",
                file=sys.stderr,
            )
            return 2
        only = {part.strip() for part in args.providers.split(",")} if args.providers else None
        for provider in configured:
            if only is not None and provider.id not in only:
                continue
            models = [
                model
                for model in provider.models
                if model.enabled and (args.model is None or model.name == args.model)
            ]
            if not args.all_models:
                models = models[:1]
            targets.extend((provider, model.name) for model in models)
        targets = targets[: args.max_targets]
    if not targets:
        print("freellmpool: no configured provider/model matched the conformance filters", file=sys.stderr)
        return 3

    store = ConformanceStore()
    rows = []
    for provider, model in targets:
        results = run_target_canaries(
            provider,
            model,
            env=env,
            features=features,
            timeout=args.timeout,
        )
        for feature, result in results.items():
            store.record(
                provider,
                model,
                feature,
                status=result["status"],
                classification=result["classification"],
            )
        rows.append({"provider": provider.id, "model": model, "features": results})
    if args.json:
        print(json.dumps(rows, separators=(",", ":"), sort_keys=True))
    else:
        for row in rows:
            print(f"{row['provider']}/{row['model']}")
            for feature, result in row["features"].items():
                print(f"  {feature:<20} {result['status']:<12} {result['classification']}")
    return 0


def _conformance_env(catalog) -> dict[str, str]:
    """Merge a bounded workflow secret map, restricted to catalog-declared names."""

    import json

    env = effective_env()
    raw = os.environ.get("FREELLMPOOL_CONFORMANCE_KEYS_JSON")
    env.pop("FREELLMPOOL_CONFORMANCE_KEYS_JSON", None)
    if not raw:
        return env
    if len(raw) > 65_536:
        raise ValueError("conformance key map exceeds size limit")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("conformance key map must be JSON") from exc
    if not isinstance(payload, dict) or len(payload) > 256:
        raise ValueError("conformance key map must be a bounded object")
    allowed = {
        name
        for provider in catalog
        for name in ((provider.key_env,) + tuple(provider.extra_env))
        if name
    }
    for name, value in payload.items():
        if name not in allowed:
            continue
        if not isinstance(value, str) or len(value) > 8_192:
            raise ValueError("conformance credential values must be bounded strings")
        env[name] = value
    return env


def cmd_conformance_status(args: argparse.Namespace) -> int:
    """Show the sanitized local protocol evidence store."""

    import json

    state = ConformanceStore().snapshot()
    if args.json:
        print(json.dumps(state, separators=(",", ":"), sort_keys=True))
        return 0
    targets = state.get("targets", {})
    if not targets:
        print("No protocol conformance evidence yet. Run `freellmpool conformance run`.")
        return 0
    for target, row in sorted(targets.items()):
        print(target)
        for feature, result in sorted(row.get("features", {}).items()):
            print(
                f"  {feature:<20} {result['status']:<12} "
                f"{result['classification']}  {result['verified_at']}"
            )
    return 0


def _in_table(name: str, table) -> bool:
    from .capability import normalize_model_name

    return normalize_model_name(name) in table


def cmd_doctor(args: argparse.Namespace) -> int:
    from .cache import default_cache_path, default_max_entries
    from .catalog import default_external_catalog_path, load_external_catalog
    from .catalog_validation import validate_catalog
    from .config import config_diagnostics, effective_env, settings
    from .key_inventory import default_config_path

    env = effective_env()
    cfg = settings()
    catalog = load_catalog()
    configured = configured_providers(catalog, env)
    pool = Pool.from_default_config()
    quota_path = pool.quota.path
    cache_path = default_cache_path()
    external_path = default_external_catalog_path()
    external = load_external_catalog(external_path)
    config_issues = config_diagnostics()
    external_note = "missing"
    if external_path.exists():
        import time

        age_hours = max(0.0, (time.time() - external_path.stat().st_mtime) / 3600.0)
        external_note = f"{age_hours:.1f}h old"
        if age_hours > 24 * 7:
            external_note += ", stale"
    errors = validate_catalog()

    print(f"freellmpool {__version__}")
    print(f"python: {sys.version.split()[0]}")
    print(f"config: {default_config_path()}")
    print(f"providers: {len(configured)}/{len(catalog)} configured")
    print(f"routing: {pool.routing}")
    print(f"quota: {quota_path} ({'exists' if quota_path.exists() else 'new'})")
    cache_ttl = env.get("FREELLMPOOL_CACHE_TTL") or cfg.get("cache_ttl", 0)
    print(f"cache: {cache_path} ttl={cache_ttl} max={default_max_entries()}")
    print(
        f"external catalog: {external_path} "
        f"({len(external)} cached provider{'s' if len(external) != 1 else ''}, {external_note})"
    )
    if config_issues:
        print("config validation: FAIL")
        for issue in config_issues[:20]:
            location = ""
            if issue.get("line") is not None:
                location += f" line={issue['line']}"
            if issue.get("column") is not None:
                location += f" column={issue['column']}"
            print(f"  - {issue.get('code', 'invalid_config')}{location}: {issue['message']}")
        if len(config_issues) > 20:
            print(f"  ... {len(config_issues) - 20} more")
    else:
        print("config validation: ok")
    if errors:
        print("catalog: FAIL")
        for error in errors[:20]:
            print(f"  - {error}")
        if len(errors) > 20:
            print(f"  ... {len(errors) - 20} more")
        return 1
    print("catalog: ok")
    return 1 if config_issues else 0


def cmd_proxy(args: argparse.Namespace) -> int:
    from .proxy import serve  # lazy: avoids http.server import on other paths
    from .tailnet import (
        SetupTokenLabel,
        UnsafeBindError,
        assert_bind_safe,
        format_setup_hints,
        is_loopback_host,
        safe_base_url,
    )

    # `--tailnet` is a Tailnet-safe alias for `freellmpool tailnet serve`.
    # It runs the same safety logic but keeps the proxy's familiar verb
    # for users who already have `freellmpool proxy` muscle memory.
    if getattr(args, "tailnet", False):
        return _run_tailnet_serve(
            port=args.port,
            api_key=args.api_key,
            allow_lan=getattr(args, "allow_lan", False),
            allow_no_auth=getattr(args, "allow_no_auth", False),
            dry_run=False,
        )

    proxy_key = (
        args.api_key
        or os.environ.get("FREELLMPOOL_PROXY_KEY")
        or settings().get("proxy_key")
        or None
    )

    # Enforce the WU-001 safety rules. Loopback binds always pass and
    # keep the legacy "warn-only" behavior below; non-loopback binds
    # require either an explicit --allow-lan + auth (or --allow-no-auth
    # as a documented escape hatch) or they are refused outright.
    try:
        assert_bind_safe(
            host=args.host,
            api_key=proxy_key,
            allow_lan=getattr(args, "allow_lan", False),
            allow_no_auth=getattr(args, "allow_no_auth", False),
        )
    except UnsafeBindError as exc:
        print(f"freellmpool: {exc}", file=sys.stderr)
        return 2

    loopback = is_loopback_host(args.host)
    if not loopback and not proxy_key:
        # Loopback-warn path is now unreachable in practice (assert_bind_safe
        # raises for non-loopback w/o auth unless allow-no-auth is set), but
        # kept as a defensive backstop in case the helper is bypassed.
        print(
            f"freellmpool: WARNING — binding to {args.host} (not loopback) with NO proxy key "
            "exposes all your configured providers to the network. Set --api-key or "
            "FREELLMPOOL_PROXY_KEY, or bind to 127.0.0.1.",
            file=sys.stderr,
        )

    pool = Pool.from_default_config()
    if not pool.providers:
        print(
            "freellmpool: no providers configured; set at least one API key "
            "(see .env.example) before starting the proxy.",
            file=sys.stderr,
        )
        return 3

    httpd = serve(pool, host=args.host, port=args.port, api_key=proxy_key)
    n_models = sum(len(p.models) for p in pool.providers)
    auth_enabled = proxy_key is not None
    auth_note = "  auth: Bearer key required\n" if auth_enabled else ""
    base_url = safe_base_url(args.host, args.port)
    print(
        f"freellmpool proxy on {base_url}/v1  "
        f"({len(pool.providers)} providers, {n_models} models)\n"
        f"{auth_note}"
        f"  point your OpenAI client at:  OPENAI_BASE_URL={base_url}/v1\n"
        f"  dashboard:  {base_url}/dashboard\n"
        f"{format_setup_hints(base_url=base_url, auth_enabled=auth_enabled, token_label=SetupTokenLabel.YOUR_PROXY_KEY)}"
        "  press Ctrl-C to stop",
        file=sys.stderr,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        s = pool.stats_snapshot()
        saved = format_saved(s["prompt_tokens"], s["completion_tokens"])
        print(
            f"\nfreellmpool: shutting down — served {s['requests']} requests · {saved}",
            file=sys.stderr,
        )
    finally:
        pool.quota.flush()
        httpd.server_close()
    return 0


def _run_tailnet_serve(
    *,
    port: int,
    api_key: str | None,
    allow_lan: bool,
    allow_no_auth: bool,
    dry_run: bool,
) -> int:
    """Shared body for ``freellmpool tailnet serve`` and ``proxy --tailnet``.

    Detects the local Tailnet IPv4, picks / validates the bind target,
    requires auth (or generates a session token), and starts the proxy
    on the Tailnet address. The two entry points only differ in their
    flag set; everything else lives here so the safety logic stays in
    one place.
    """
    from .proxy import serve  # lazy: avoids http.server import on other paths
    from .tailnet import (
        STATE_CLI_MISSING,
        STATE_LOGGED_OUT,
        STATE_MALFORMED,
        STATE_NO_IPV4,
        SetupTokenLabel,
        UnsafeBindError,
        _disclose_session_token_to_tty,
        _NonTTYDisclosureError,
        assert_bind_safe,
        detect_tailnet,
        format_setup_hints,
        generate_session_token,
        safe_base_url,
    )

    status = detect_tailnet()
    if not status.usable:
        # Map tagged states to actionable error messages. We never echo
        # subprocess stderr (it can include MagicDNS hostnames and other
        # text the user may not want echoed back), and we never print
        # provider API keys.
        if status.state == STATE_CLI_MISSING:
            print(
                "freellmpool: cannot start Tailnet serving — "
                f"{status.detail}",
                file=sys.stderr,
            )
        elif status.state == STATE_LOGGED_OUT:
            print(
                "freellmpool: cannot start Tailnet serving — "
                f"{status.detail}",
                file=sys.stderr,
            )
        elif status.state == STATE_NO_IPV4:
            print(
                "freellmpool: cannot start Tailnet serving — "
                f"{status.detail}",
                file=sys.stderr,
            )
        elif status.state == STATE_MALFORMED:
            print(
                "freellmpool: cannot start Tailnet serving — "
                f"{status.detail}",
                file=sys.stderr,
            )
        else:  # pragma: no cover - defensive
            print(
                "freellmpool: cannot start Tailnet serving — "
                f"unknown Tailscale state: {status.state}",
                file=sys.stderr,
            )
        print(
            "\nHint: `freellmpool proxy` on loopback (127.0.0.1) still works "
            "without Tailscale.",
            file=sys.stderr,
        )
        return 3

    # We have a validated 100.x address. Resolve the auth key: explicit
    # --api-key > FREELLMPOOL_PROXY_KEY > config proxy_key > generate one.
    explicit_key = (
        api_key
        or os.environ.get("FREELLMPOOL_PROXY_KEY")
        or settings().get("proxy_key")
        or None
    )

    bind_host = status.ipv4  # type: ignore[assignment]  # safe: usable=True

    # Non-Tailnet LAN binds need --allow-lan; even Tailnet binds need
    # auth unless --allow-no-auth is passed. assert_bind_safe enforces
    # the WU-001 spec rules.
    needs_session_token = explicit_key is None and not allow_no_auth
    if (
        needs_session_token
        and not dry_run
        and not bool(getattr(sys.stderr, "isatty", lambda: False)())
    ):
        print(
            "freellmpool: refusing to print an auto-generated proxy key in a "
            "non-interactive session. Pass --api-key, set "
            "FREELLMPOOL_PROXY_KEY, or run from an interactive terminal.",
            file=sys.stderr,
        )
        return 2

    if needs_session_token:
        # Default-on safety: generate a session token so a Tailnet serve
        # never silently starts unauthenticated. Live runs reach this
        # point only on a TTY, where the token is shown once and never
        # persisted; dry-runs expose only a marker.
        explicit_key = generate_session_token()
        generated_session_token = True
    else:
        generated_session_token = False

    try:
        assert_bind_safe(
            host=bind_host,
            api_key=explicit_key,
            allow_lan=allow_lan,
            allow_no_auth=allow_no_auth,
        )
    except UnsafeBindError as exc:
        print(f"freellmpool: {exc}", file=sys.stderr)
        return 2

    base_url = safe_base_url(bind_host, port)
    if generated_session_token:
        token_label = (
            SetupTokenLabel.SESSION_REAL_RUN
            if dry_run
            else SetupTokenLabel.SESSION_DISCLOSED
        )
    elif explicit_key:
        token_label = SetupTokenLabel.YOUR_PROXY_KEY
    else:
        token_label = SetupTokenLabel.PROXY_KEY
    auth_enabled = explicit_key is not None
    setup_block = format_setup_hints(
        base_url=base_url,
        auth_enabled=auth_enabled,
        token_label=token_label,
    )
    auth_status = (
        "Bearer key required (token generated for this session)"
        if generated_session_token
        else (
            "Bearer key required (using --api-key / FREELLMPOOL_PROXY_KEY)"
            if auth_enabled
            else "no auth (--allow-no-auth)"
        )
    )

    if dry_run:
        # Print the exact bind address, auth status, dashboard URL and
        # client env vars — but never start the server and never print
        # the actual token (the plan asks for a "token marker"; the
        # real token is only shown for live runs, never in --dry-run,
        # so a dry-run can be safely pasted into a chat / issue).
        marker = token_label.value if auth_enabled else "<none>"
        print(
            f"freellmpool tailnet serve (dry run)\n"
            f"  bind host      : {bind_host}\n"
            f"  bind URL       : {base_url}/v1\n"
            f"  dashboard      : {base_url}/dashboard\n"
            f"  auth status    : {auth_status}\n"
            f"  token marker   : {marker}\n"
            f"\n{setup_block}",
            file=sys.stderr,
        )
        return 0

    pool = Pool.from_default_config()
    if not pool.providers:
        print(
            "freellmpool: no providers configured; set at least one API key "
            "(see .env.example) before starting the proxy.",
            file=sys.stderr,
        )
        return 3

    if generated_session_token:
        # The private helper is the sole intentional secret-output boundary.
        # It re-checks TTY status and performs one direct write, keeping the
        # secret out of generic printing, logging, setup, and banner paths.
        if explicit_key is None:  # pragma: no cover - defensive invariant
            print(
                "freellmpool: failed to generate a one-session proxy key.",
                file=sys.stderr,
            )
            return 2
        try:
            _disclose_session_token_to_tty(explicit_key, stream=sys.stderr)
        except _NonTTYDisclosureError:
            print(
                "freellmpool: refusing to disclose an auto-generated proxy key "
                "without an interactive terminal.",
                file=sys.stderr,
            )
            return 2

    httpd = serve(pool, host=bind_host, port=port, api_key=explicit_key)
    n_models = sum(len(p.models) for p in pool.providers)
    print(
        f"freellmpool tailnet serve on {base_url}/v1  "
        f"({len(pool.providers)} providers, {n_models} models)\n"
        f"  auth status    : {auth_status}\n"
        f"  bind address   : {bind_host} (Tailnet IPv4)\n"
        f"\n{setup_block}"
        "  press Ctrl-C to stop",
        file=sys.stderr,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        s = pool.stats_snapshot()
        saved = format_saved(s["prompt_tokens"], s["completion_tokens"])
        print(
            f"\nfreellmpool: shutting down — served {s['requests']} requests · {saved}",
            file=sys.stderr,
        )
    finally:
        pool.quota.flush()
        httpd.server_close()
    return 0


def cmd_tailnet_status(args: argparse.Namespace) -> int:
    from .tailnet import (
        STATE_CLI_MISSING,
        STATE_LOGGED_OUT,
        STATE_MALFORMED,
        STATE_NO_IPV4,
        STATE_USABLE,
        detect_tailnet,
    )

    status = detect_tailnet()
    if status.state == STATE_USABLE:
        print(
            f"freellmpool tailnet: usable\n"
            f"  IPv4           : {status.ipv4}\n"
            f"  next           : `freellmpool tailnet serve --port {args.port}` "
            "(auth is auto-generated if you don't pass --api-key)",
            file=sys.stderr,
        )
        return 0

    label = {
        STATE_CLI_MISSING: "tailscale CLI missing",
        STATE_LOGGED_OUT: "tailscale logged out / daemon unreachable",
        STATE_NO_IPV4: "no IPv4 from tailscale",
        STATE_MALFORMED: "malformed tailscale output",
    }.get(status.state, status.state)
    print(
        f"freellmpool tailnet: {label}\n"
        f"  reason         : {status.detail}\n"
        f"  fallback       : `freellmpool proxy` on 127.0.0.1 still works.",
        file=sys.stderr,
    )
    return 1


def cmd_tailnet_serve(args: argparse.Namespace) -> int:
    return _run_tailnet_serve(
        port=args.port,
        api_key=args.api_key,
        allow_lan=args.allow_lan,
        allow_no_auth=args.allow_no_auth,
        dry_run=args.dry_run,
    )


def cmd_tailnet_connect(args: argparse.Namespace) -> int:
    """Print the client setup commands for a remote tailnet proxy.

    ``host`` may be a 100.x Tailnet IPv4 or a MagicDNS hostname; we do
    not resolve it (no DNS / no network). The user pastes the printed
    exports into the client machine.
    """
    from .tailnet import SetupTokenLabel, format_setup_hints, safe_base_url

    host = args.host
    if not host:
        print("freellmpool: tailnet connect needs a tailnet host or IP", file=sys.stderr)
        return 2

    base = safe_base_url(host, args.port)
    print(
        f"freellmpool tailnet connect: {host}:{args.port}\n"
        f"{format_setup_hints(base_url=base, auth_enabled=True, token_label=SetupTokenLabel.PROXY_KEY_FROM_SERVER)}"
        "\n  If the server was started with --allow-no-auth, any placeholder API key works.",
        file=sys.stderr,
    )
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    from .init_wizard import (
        detect_environment,
        interactive_choice_to_plan,
        render_detect_only,
        render_interactive_intro,
        render_setup_plan,
        report_to_json,
    )

    report = detect_environment()
    if args.json:
        print(report_to_json(report))
        return 0

    if not args.yes and not args.agent and not args.tailnet:
        print(render_interactive_intro(report))
        try:
            choice = input("Setup path: ")
        except EOFError:
            choice = ""
        plan = interactive_choice_to_plan(choice)
        if plan is None:
            return 0
        agent, tailnet = plan
        print(render_setup_plan(report, agent=agent, tailnet=tailnet, port=args.port))
        return 0

    if args.yes and not args.agent and not args.tailnet:
        print(render_detect_only(report, port=args.port))
        return 0

    try:
        print(
            render_setup_plan(
                report,
                agent=args.agent,
                tailnet=args.tailnet,
                port=args.port,
                force=args.force,
            )
        )
    except ValueError as exc:
        print(f"freellmpool: {exc}", file=sys.stderr)
        return 3
    return 0


def cmd_battle(args: argparse.Namespace) -> int:
    from .battle import render_battle_markdown, run_battle

    stdin = _read_stdin()
    prompt = args.prompt or ""
    if stdin:
        prompt = f"{stdin}\n\n{prompt}".strip() if prompt else stdin
    if not prompt.strip():
        print("freellmpool: no prompt provided (pass text or pipe stdin)", file=sys.stderr)
        return 3

    pool = Pool.from_default_config()
    result = run_battle(
        pool,
        prompt,
        n=args.models,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        routing=routing_override(args.routing) if args.routing is not None else "quality",
        synthesize=args.synthesize,
    )
    if not result.answers:
        print("freellmpool: no providers configured", file=sys.stderr)
        return 3
    if result.truncated:
        print(
            f"freellmpool: battle ran {len(result.answers)} model(s) "
            f"after requesting {result.requested_count}",
            file=sys.stderr,
        )
    provider_count = len({answer.provider_id for answer in result.answers})
    if provider_count < 3:
        print(
            f"freellmpool: only {provider_count} configured provider(s) available for battle",
            file=sys.stderr,
        )
    print(render_battle_markdown(result))
    return 0 if result.successful_answers else 4


def cmd_playground(args: argparse.Namespace) -> int:
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    base = f"http://127.0.0.1:{args.port}"
    try:
        req = urllib.request.Request(f"{base}/playground")
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(req, timeout=1.5) as resp:  # noqa: S310
            if resp.status == 200 and "text/html" in resp.headers.get("Content-Type", ""):
                print(f"{base}/playground")
                return 0
    except (OSError, urllib.error.URLError):
        pass
    print(
        f"freellmpool playground: no proxy reachable on {base}. "
        f"Start one with `freellmpool proxy --port {args.port}`, then open {base}/playground.",
        file=sys.stderr,
    )
    return 3


def cmd_recipe_list(args: argparse.Namespace) -> int:
    import json

    from .recipes import list_recipes, list_recipes_json

    if args.json:
        print(json.dumps(list_recipes_json(), indent=2, sort_keys=True))
        return 0
    for recipe in list_recipes():
        print(f"{recipe.name}\t{recipe.description}")
    return 0


def cmd_recipe_show(args: argparse.Namespace) -> int:
    import json

    from .recipes import RecipeError, get_recipe, render_recipe

    try:
        recipe = get_recipe(args.name)
    except RecipeError as exc:
        print(f"freellmpool recipe: {exc}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(recipe.summary(), indent=2, sort_keys=True))
    else:
        print(render_recipe(recipe))
    return 0


def cmd_recipe_run(args: argparse.Namespace) -> int:
    from .recipes import (
        RecipeError,
        collect_recipe_input,
        get_recipe,
        run_recipe,
    )

    try:
        recipe = get_recipe(args.name)
        stdin = _read_stdin()
        input_text, path = collect_recipe_input(
            recipe,
            prompt=args.prompt or "",
            stdin=stdin,
            input_file=args.input,
            path=args.path,
        )
        validation_output = args.validation_output
        if args.validation_output_file:
            validation_output = Path(args.validation_output_file).read_text(encoding="utf-8")
        result = run_recipe(
            Pool.from_default_config(),
            recipe,
            input_text=input_text,
            path=path,
            validation_output=validation_output,
            opinions=args.opinions,
            synthesize=args.synthesize,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
        )
    except (RecipeError, NoProvidersConfigured, AllProvidersExhausted) as exc:
        print(f"freellmpool recipe: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"freellmpool recipe: {exc}", file=sys.stderr)
        return 3
    print(result.output)
    return 0


def cmd_jobs_add(args: argparse.Namespace) -> int:
    """Queue a recipe or ask job to the local JSONL store."""
    from .jobs import JOB_KIND_ASK, JOB_KIND_RECIPE, JobError, JobSpec, JobStore

    try:
        if args.recipe:
            spec_payload: dict[str, object] = {
                "kind": JOB_KIND_RECIPE,
                "recipe": args.recipe,
                "prompt": args.prompt or "",
            }
            if args.input:
                spec_payload["input"] = args.input
            if args.path:
                spec_payload["path"] = args.path
            if args.validation_output is not None:
                spec_payload["validation_output"] = args.validation_output
            if args.validation_output_file:
                spec_payload["validation_output_file"] = args.validation_output_file
            spec_payload["opinions"] = args.opinions
            spec_payload["synthesize"] = bool(args.synthesize)
            if args.max_tokens is not None:
                spec_payload["max_tokens"] = args.max_tokens
            spec_payload["timeout"] = args.timeout
            dedupe = args.recipe if args.dedupe else None
        elif args.role or args.prompt:
            if args.role and not args.prompt:
                print(
                    "freellmpool jobs add: --role requires a prompt",
                    file=sys.stderr,
                )
                return 2
            spec_payload = {
                "kind": JOB_KIND_ASK,
                "role": args.role,
                "prompt": args.prompt or "",
            }
            if args.max_tokens is not None:
                spec_payload["max_tokens"] = args.max_tokens
            spec_payload["timeout"] = args.timeout
            dedupe = args.role if args.dedupe else None
        else:
            print(
                "freellmpool jobs add: provide --recipe <name> [prompt] or --role <role> with a prompt",
                file=sys.stderr,
            )
            return 2
        store = JobStore()
        kind = str(spec_payload["kind"])
        job = store.add(JobSpec(kind=kind, payload=spec_payload, dedupe_key=dedupe))
    except JobError as exc:
        print(f"freellmpool jobs add: {exc}", file=sys.stderr)
        return 3

    print(job.job_id)
    return 0


def cmd_jobs_list(args: argparse.Namespace) -> int:
    from .jobs import JobStore, render_jobs

    store = JobStore()
    if args.status:
        statuses = {s.strip() for s in args.status.split(",") if s.strip()}
        jobs = [job for job in store.jobs() if job.status in statuses]
    else:
        jobs = store.jobs()
    print(render_jobs(jobs))
    return 0


def cmd_jobs_run(args: argparse.Namespace) -> int:
    """Process pending queued jobs in the foreground."""
    from .jobs import (
        JobError,
        JobStore,
        render_run_plan,
        render_run_summary,
        run_pending_jobs,
    )

    # Validate runner bounds before branching so dry-run and real execution
    # reject the same invalid command line without reading the job store.
    if args.limit is not None and args.limit < 1:
        print("freellmpool jobs run: --limit must be >= 1", file=sys.stderr)
        return 3
    if args.max_failures is not None and args.max_failures < 1:
        print("freellmpool jobs run: --max-failures must be >= 1", file=sys.stderr)
        return 3

    store = JobStore()
    if args.dry_run:
        # Dry-run prints the same limited execution subset a real limited
        # run would process, but it never mutates the queue. We honour
        # ``--max-failures`` shape-wise too: --max-failures N still
        # affects the real runner, not the dry-run plan renderer, so we
        # do not pass it here.
        try:
            plan = render_run_plan(store.pending(), limit=args.limit)
        except JobError as exc:
            print(f"freellmpool jobs run: {exc}", file=sys.stderr)
            return 3
        print(plan)
        return 0
    try:
        outcome = run_pending_jobs(
            store,
            dry_run=False,
            max_failures=args.max_failures,
            limit=args.limit,
        )
    except JobError as exc:
        print(f"freellmpool jobs run: {exc}", file=sys.stderr)
        return 3

    print(render_run_summary(outcome))
    if outcome.halted_by_max_failures:
        return 5
    if outcome.had_failures:
        return 4
    return 0


def cmd_jobs_watch(args: argparse.Namespace) -> int:
    """Render the replayed job queue once (refresh mode)."""
    from .jobs import JobStore, render_jobs

    store = JobStore()
    print(render_jobs(store.jobs()))
    return 0


def cmd_jobs_cancel(args: argparse.Namespace) -> int:
    from .jobs import JobError, JobStore, UnknownJobError

    store = JobStore()
    try:
        job = store.cancel(args.job_id)
    except UnknownJobError as exc:
        print(f"freellmpool jobs cancel: {exc}", file=sys.stderr)
        return 3
    except JobError as exc:
        print(f"freellmpool jobs cancel: {exc}", file=sys.stderr)
        return 3

    print(f"{job.job_id} {job.status}")
    return 0


def cmd_report_list(args: argparse.Namespace) -> int:
    from .artifacts import RunRecordStore
    from .reports import render_record_list

    store = RunRecordStore()
    print(render_record_list(store.recent(limit=args.limit)))
    return 0


def cmd_report_last(args: argparse.Namespace) -> int:
    from .artifacts import RunRecordStore
    from .reports import open_report_path, render_html_report, render_markdown_report, write_report

    store = RunRecordStore()
    record = store.last()
    if record is None:
        print("freellmpool report: no run records found", file=sys.stderr)
        return 3
    fmt = "html" if args.html else "md"
    path = write_report(record, fmt, store=store)
    if args.open:
        open_report_path(path)
        return 0
    if args.path:
        print(path)
        return 0
    if args.html:
        print(render_html_report(record))
    else:
        print(render_markdown_report(record), end="")
    return 0


def cmd_report_open(args: argparse.Namespace) -> int:
    from .artifacts import RunRecordStore
    from .reports import open_report_path, resolve_report_target, write_report

    store = RunRecordStore()
    record, path = resolve_report_target(store, args.target)
    if record is None and path is None:
        print("freellmpool report: no run records found", file=sys.stderr)
        return 3
    if record is not None:
        path = write_report(record, "html", store=store)
    assert path is not None
    if not path.exists():
        print(f"freellmpool report: report not found: {path}", file=sys.stderr)
        return 3
    open_report_path(path)
    return 0


def cmd_cost_show(args: argparse.Namespace) -> int:
    from .artifacts import RunRecordStore
    from .reports import render_cost_report

    record = RunRecordStore().get(args.run_id)
    if record is None:
        print(
            f"freellmpool cost: run not found: {args.run_id}. "
            "Run `freellmpool report list` to see available runs.",
            file=sys.stderr,
        )
        return 3
    print(render_cost_report(record))
    return 0


def cmd_profile_list(args: argparse.Namespace) -> int:
    from .profiles import render_profile_list

    print(render_profile_list())
    return 0


def cmd_profile_show(args: argparse.Namespace) -> int:
    from .profiles import get_profile, render_profile

    profile = get_profile(args.name)
    if profile is None:
        print(f"freellmpool: unknown profile '{args.name}'", file=sys.stderr)
        return 3
    print(render_profile(profile))
    return 0


def cmd_profile_install(args: argparse.Namespace) -> int:
    from .profiles import get_profile, render_profile_quickstart

    profile = get_profile(args.name)
    if profile is None:
        print(f"freellmpool: unknown profile '{args.name}'", file=sys.stderr)
        return 3
    print(render_profile_quickstart(profile))
    print("\nConfig snippets:")
    for label, snippet in sorted(profile.config_snippets.items()):
        print(f"\n--- {label} ---")
        print(snippet)
    return 0


def cmd_profile_doctor(args: argparse.Namespace) -> int:
    import math

    from .config import effective_env, settings
    from .profiles import (
        get_profile,
        profile_with_base_url,
        render_doctor_plan,
        run_doctor,
    )

    profile = get_profile(args.name)
    if profile is None:
        print(f"freellmpool: unknown profile '{args.name}'", file=sys.stderr)
        return 3
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        print(
            "freellmpool profile doctor: timeout must be a finite positive number",
            file=sys.stderr,
        )
        return 3
    if args.base_url:
        try:
            profile = profile_with_base_url(profile, args.base_url)
        except (TypeError, ValueError):
            print("freellmpool profile doctor: invalid --base-url", file=sys.stderr)
            return 3
    if args.dry_run:
        print(render_doctor_plan(profile))
        return 0
    env = effective_env()
    proxy_key = env.get("FREELLMPOOL_PROXY_KEY") or settings().get("proxy_key")
    code, lines = run_doctor(
        profile,
        timeout=args.timeout,
        env=env,
        proxy_key=str(proxy_key) if proxy_key else None,
    )
    print("\n".join(lines))
    return code


def cmd_code(args: argparse.Namespace) -> int:
    from .agents import list_agents, render

    if not args.agent:
        print(list_agents())
        return 0
    out = render(args.agent.lower())
    if out is None:
        print(f"freellmpool: unknown agent '{args.agent}'\n", file=sys.stderr)
        print(list_agents(), file=sys.stderr)
        return 3
    print(out)
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import serve_stdio  # lazy import

    pool = Pool.from_default_config()
    print(
        f"freellmpool MCP server (stdio) — {len(pool.providers)} providers ready. "
        "Add to your MCP client config; see docs/MCP.md.",
        file=sys.stderr,
    )
    try:
        serve_stdio(pool, version=__version__)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        pool.quota.flush()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="freellmpool",
        description="Pool free-tier LLM APIs behind one OpenAI-compatible endpoint.",
    )
    parser.add_argument("--version", action="version", version=f"freellmpool {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ask = sub.add_parser("ask", help="one-shot completion")
    p_ask.add_argument("prompt", nargs="?", default="", help="prompt text (stdin is appended)")
    p_ask.add_argument("-s", "--system", help="system prompt")
    p_ask.add_argument(
        "-m", "--model", help="model name, or provider/model (e.g. groq/llama-3.3-70b-versatile)"
    )
    p_ask.add_argument("-p", "--providers", help="comma-separated provider ids to allow")
    p_ask.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="max output tokens (default: 1024, or the role's default)",
    )
    p_ask.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="sampling temperature (default: 0.0, or the role's default)",
    )
    p_ask.add_argument("--timeout", type=float, default=90.0, help="upstream provider timeout seconds")
    p_ask.add_argument(
        "-r",
        "--role",
        help="use a role preset (see `freellmpool roles`)",
    )
    p_ask.add_argument(
        "--routing",
        choices=PUBLIC_ROUTING_ALIASES,
        help="routing mode override (auto uses the pool default)",
    )
    p_ask.add_argument(
        "--task",
        choices=TASK_HINTS,
        help="task hint for quality routing (auto classifies locally)",
    )
    p_ask.add_argument(
        "--mode",
        choices=["normal", "wise"],
        help="per-command quota mode override (default: FREELLMPOOL_MODE or config)",
    )
    p_ask.add_argument(
        "--second-opinion",
        action="store_true",
        help="ask a small panel of diverse free models instead of one model",
    )
    p_ask.add_argument(
        "--opinions",
        type=int,
        default=3,
        help="number of models for --second-opinion (clamped to 2-5)",
    )
    p_ask.add_argument(
        "--synthesize",
        action="store_true",
        help="append a quality-routed synthesis for --second-opinion",
    )
    p_ask.add_argument(
        "--json", action="store_true", help="ask for JSON output and strip code fences"
    )
    p_ask.add_argument("-v", "--verbose", action="store_true", help="report which provider served")
    p_ask.set_defaults(func=cmd_ask)

    p_roles = sub.add_parser("roles", help="list available ask roles")
    p_roles.set_defaults(func=cmd_roles)

    p_tokenmax = sub.add_parser(
        "tokenmax",
        help="🌈 fan out to eligible ranked targets (max 256), then synthesize",
        description=(
            "Fan a prompt out to automatically eligible ranked targets across configured "
            "providers (hard cap 256; --max-models, routing, or wise mode may narrow the "
            "swarm), then synthesize the returned responses."
        ),
    )
    p_tokenmax.add_argument("prompt", nargs="?", default="", help="prompt text (stdin is appended)")
    p_tokenmax.add_argument("-s", "--system", help="system prompt")
    p_tokenmax.add_argument(
        "--max-models",
        type=int,
        default=None,
        help="narrow the eligible ranked target set (default: all eligible, hard cap 256)",
    )
    p_tokenmax.add_argument(
        "--max-tokens", type=int, default=400, help="max output tokens per model"
    )
    p_tokenmax.add_argument(
        "--timeout", type=float, default=90.0, help="upstream provider timeout seconds"
    )
    p_tokenmax.add_argument(
        "--no-synthesize",
        action="store_true",
        help="just dump every answer; skip the synthesized verdict",
    )
    p_tokenmax.add_argument(
        "--mode",
        choices=["normal", "wise"],
        help="per-command quota mode override (default: FREELLMPOOL_MODE or config)",
    )
    p_tokenmax.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="confirm wise-mode fan-out prompts",
    )
    p_tokenmax.set_defaults(func=cmd_tokenmax)

    p_battle = sub.add_parser("battle", help="compare a prompt across a small model panel")
    p_battle.add_argument("prompt", nargs="?", default="", help="prompt text (stdin is appended)")
    p_battle.add_argument(
        "-n",
        "--models",
        type=int,
        default=3,
        help="number of models to compare (clamped to 2-5)",
    )
    p_battle.add_argument(
        "--max-tokens", type=int, default=512, help="max output tokens per model"
    )
    p_battle.add_argument("--timeout", type=float, default=90.0, help="upstream timeout seconds")
    p_battle.add_argument(
        "--routing",
        choices=PUBLIC_ROUTING_ALIASES,
        default="quality",
        help="routing mode for candidate selection",
    )
    p_battle.add_argument(
        "--synthesize",
        action="store_true",
        help="append a synthesis using the shared second-opinion synthesis path",
    )
    p_battle.set_defaults(func=cmd_battle)

    p_playground = sub.add_parser("playground", help="print the local playground URL")
    p_playground.add_argument("--port", type=int, default=8080, help="proxy port")
    p_playground.set_defaults(func=cmd_playground)

    p_recipe = sub.add_parser("recipe", help="run bundled JSON workflow recipes")
    recipe_sub = p_recipe.add_subparsers(dest="recipe_command", required=True)
    p_recipe_list = recipe_sub.add_parser("list", help="list bundled recipes")
    p_recipe_list.add_argument("--json", action="store_true", help="emit versioned JSON")
    p_recipe_list.set_defaults(func=cmd_recipe_list)
    p_recipe_show = recipe_sub.add_parser("show", help="show a bundled recipe")
    p_recipe_show.add_argument("name", help="recipe name")
    p_recipe_show.add_argument("--json", action="store_true", help="emit JSON")
    p_recipe_show.set_defaults(func=cmd_recipe_show)
    p_recipe_run = recipe_sub.add_parser("run", help="run a bundled recipe")
    p_recipe_run.add_argument("name", help="recipe name")
    p_recipe_run.add_argument("prompt", nargs="?", default="", help="prompt text input")
    p_recipe_run.add_argument("--input", help="read recipe input from a file")
    p_recipe_run.add_argument("--path", help="read files matching a path/glob for path recipes")
    p_recipe_run.add_argument("--validation-output", help="validation output text")
    p_recipe_run.add_argument("--validation-output-file", help="read validation output from a file")
    p_recipe_run.add_argument("--opinions", type=int, default=3, help="panel size for panel recipes")
    p_recipe_run.add_argument("--synthesize", action="store_true", help="append panel synthesis")
    p_recipe_run.add_argument("--max-tokens", type=int, default=None, help="max output tokens")
    p_recipe_run.add_argument("--timeout", type=float, default=90.0, help="upstream timeout seconds")
    p_recipe_run.set_defaults(func=cmd_recipe_run)

    p_jobs = sub.add_parser(
        "jobs",
        help="local foreground job queue for slow, quota-aware work",
    )
    jobs_sub = p_jobs.add_subparsers(dest="jobs_command", required=True)

    p_jobs_add = jobs_sub.add_parser("add", help="queue a recipe or ask job")
    p_jobs_add.add_argument("--recipe", help="bundle a named recipe as the job's payload")
    p_jobs_add.add_argument(
        "--role", help="queue an ask job with a role preset (requires a prompt)"
    )
    p_jobs_add.add_argument("prompt", nargs="?", default="", help="prompt or recipe text")
    p_jobs_add.add_argument("--input", help="recipe input file")
    p_jobs_add.add_argument("--path", help="recipe path glob (path recipes)")
    p_jobs_add.add_argument("--validation-output", help="recipe validation output text")
    p_jobs_add.add_argument(
        "--validation-output-file", help="read recipe validation output from a file"
    )
    p_jobs_add.add_argument(
        "--opinions", type=int, default=3, help="panel size for panel recipes"
    )
    p_jobs_add.add_argument("--synthesize", action="store_true", help="append panel synthesis")
    p_jobs_add.add_argument("--max-tokens", type=int, default=None, help="max output tokens")
    p_jobs_add.add_argument("--timeout", type=float, default=90.0, help="upstream timeout seconds")
    p_jobs_add.add_argument(
        "--dedupe",
        action="store_true",
        help="reject re-submission of the same recipe/role while pending",
    )
    p_jobs_add.set_defaults(func=cmd_jobs_add)

    p_jobs_list = jobs_sub.add_parser("list", help="show replayed queue state")
    p_jobs_list.add_argument(
        "--status",
        help="comma-separated status filter (pending, running, completed, failed, cancelled)",
    )
    p_jobs_list.set_defaults(func=cmd_jobs_list)

    p_jobs_run = jobs_sub.add_parser(
        "run",
        help="process pending jobs in the foreground (no daemon)",
    )
    p_jobs_run.add_argument("--limit", type=int, default=None, help="max jobs to run this invocation")
    p_jobs_run.add_argument(
        "--max-failures",
        type=int,
        default=None,
        help="stop after N consecutive failed executions in this run",
    )
    p_jobs_run.add_argument(
        "--dry-run",
        action="store_true",
        help="print execution order without mutating the queue",
    )
    p_jobs_run.set_defaults(func=cmd_jobs_run)

    p_jobs_watch = jobs_sub.add_parser(
        "watch",
        help="render the replayed queue (refresh; no daemon)",
    )
    p_jobs_watch.set_defaults(func=cmd_jobs_watch)

    p_jobs_cancel = jobs_sub.add_parser(
        "cancel",
        help="append a cancel tombstone for a queued or running job",
    )
    p_jobs_cancel.add_argument("job_id", help="job id from `freellmpool jobs list`")
    p_jobs_cancel.set_defaults(func=cmd_jobs_cancel)

    p_report = sub.add_parser("report", help="render local Markdown/HTML run reports")
    report_sub = p_report.add_subparsers(dest="report_command", required=True)
    p_report_list = report_sub.add_parser("list", help="list recent run records")
    p_report_list.add_argument("--limit", type=int, default=20, help="number of records to show")
    p_report_list.set_defaults(func=cmd_report_list)
    p_report_last = report_sub.add_parser("last", help="render the newest valid run record")
    p_report_last.add_argument(
        "--markdown",
        action="store_true",
        help="render Markdown (default unless --html is passed)",
    )
    p_report_last.add_argument("--html", action="store_true", help="render HTML")
    p_report_last.add_argument("--path", action="store_true", help="print the written report path")
    p_report_last.add_argument("--open", action="store_true", help="open the written report path")
    p_report_last.set_defaults(func=cmd_report_last)
    p_report_open = report_sub.add_parser(
        "open", help="open a generated report path or render/open a run id"
    )
    p_report_open.add_argument("target", nargs="?", help="run id or report file path")
    p_report_open.set_defaults(func=cmd_report_open)

    p_cost = sub.add_parser("cost", help="show local cost/quota audits for run records")
    cost_sub = p_cost.add_subparsers(dest="cost_command", required=True)
    p_cost_show = cost_sub.add_parser("show", help="show estimated cost avoided for a run")
    p_cost_show.add_argument("run_id", help="run id from `freellmpool report list`")
    p_cost_show.set_defaults(func=cmd_cost_show)

    p_prov = sub.add_parser("providers", help="list providers and configuration status")
    prov_sub = p_prov.add_subparsers(dest="providers_command")
    p_prov_health = prov_sub.add_parser(
        "health", help="test configured providers with a tiny request"
    )
    p_prov_health.add_argument("-m", "--model", help="pin one model name to test on every provider")
    p_prov_health.add_argument("-p", "--providers", help="comma-separated provider ids to test")
    p_prov_health.add_argument(
        "--timeout", type=float, default=20.0, help="per-call timeout seconds"
    )
    p_prov_health.set_defaults(func=cmd_providers_health)
    p_prov.set_defaults(func=cmd_providers)

    p_models = sub.add_parser("models", help="list every available provider/model id")
    p_models.add_argument("-p", "--providers", help="comma-separated provider ids to filter")
    p_models.add_argument(
        "-c", "--configured-only", action="store_true", help="only show configured providers"
    )
    p_models.add_argument(
        "--all", action="store_true", help="include models that are off by default"
    )
    p_models.add_argument("--json", action="store_true", help="emit a machine-readable JSON list")
    p_models.set_defaults(func=cmd_models)

    p_quota = sub.add_parser("quota", help="show today's per-provider usage")
    p_quota.set_defaults(func=cmd_quota)

    p_quota_wise = sub.add_parser("quota-wise", help="inspect quota-wise mode headroom")
    quota_wise_sub = p_quota_wise.add_subparsers(dest="quota_wise_command", required=True)
    p_quota_wise_status = quota_wise_sub.add_parser(
        "status", help="show local headroom and the recommended mode"
    )
    p_quota_wise_status.set_defaults(func=cmd_quota_wise_status)

    p_stats = sub.add_parser(
        "stats", help="lifetime usage totals (tokens served free, estimated cost avoided)"
    )
    p_stats.set_defaults(func=cmd_stats)

    p_badge = sub.add_parser("badge", help="render a shareable SVG badge/summary of lifetime usage")
    p_badge.add_argument(
        "--summary", action="store_true", help="render the larger summary card instead of a badge"
    )
    p_badge.add_argument(
        "--metric",
        choices=["tokens", "saved", "requests"],
        default="tokens",
        help="which figure the badge shows (default: tokens)",
    )
    p_badge.add_argument("-o", "--output", help="write the SVG to this file instead of stdout")
    p_badge.set_defaults(func=cmd_badge)

    p_keys = sub.add_parser("keys", help="inspect manually configured provider keys")
    keys_sub = p_keys.add_subparsers(dest="keys_command", required=True)
    p_keys_status = keys_sub.add_parser("status", help="show key inventory and provider readiness")
    p_keys_status.add_argument(
        "--target", type=int, default=5, help="desired healthy provider count"
    )
    p_keys_status.add_argument("--all", action="store_true", help="include missing providers")
    p_keys_status.set_defaults(func=cmd_keys_status)
    p_keys_checklist = keys_sub.add_parser(
        "checklist", help="manual actions to reach target capacity"
    )
    p_keys_checklist.add_argument(
        "--target", type=int, default=5, help="desired healthy provider count"
    )
    p_keys_checklist.set_defaults(func=cmd_keys_checklist)
    p_keys_add = keys_sub.add_parser(
        "add", help="store a provider key and print newly unlocked model routes"
    )
    p_keys_add.add_argument("provider_arg", nargs="?", help="provider id or external provider name")
    p_keys_add.add_argument("-p", "--provider")
    p_keys_add.add_argument("--value")
    p_keys_add.add_argument("--base-url", help="OpenAI-compatible base URL for a new provider")
    p_keys_add.add_argument("--model", help="default model id for a new provider")
    p_keys_add.add_argument("--label")
    p_keys_add.add_argument("--notes")
    p_keys_add.add_argument("--commercial-allowed", action="store_true")
    p_keys_add.add_argument("-y", "--yes", action="store_true")
    p_keys_add.set_defaults(func=cmd_keys_add)

    p_local = sub.add_parser(
        "local",
        help="explicitly discover and import loopback OpenAI-compatible runtimes",
    )
    local_sub = p_local.add_subparsers(dest="local_command", required=True)
    p_local_discover = local_sub.add_parser(
        "discover",
        help="preview models from fixed or explicit literal-loopback endpoints",
    )
    p_local_discover.add_argument(
        "--name",
        help="runtime id (lm_studio, ollama, llama_cpp, or a custom lowercase id)",
    )
    p_local_discover.add_argument(
        "--base-url",
        help="explicit http://127.x.x.x:port/v1 or http://[::1]:port/v1 endpoint",
    )
    p_local_discover.add_argument(
        "--timeout", type=float, default=1.0, help="per-endpoint timeout, clamped to 0.05..5s"
    )
    p_local_discover.add_argument("--json", action="store_true", help="emit sanitized JSON")
    p_local_discover.set_defaults(func=cmd_local_discover)
    p_local_import = local_sub.add_parser(
        "import",
        help="affirmatively add one discovered runtime as a reversible pin-only provider",
    )
    p_local_import.add_argument(
        "--name",
        required=True,
        help="runtime id (lm_studio, ollama, llama_cpp, or a custom lowercase id)",
    )
    p_local_import.add_argument(
        "--base-url",
        help="explicit literal-loopback endpoint (required for custom runtime ids)",
    )
    p_local_import.add_argument(
        "--timeout", type=float, default=1.0, help="discovery timeout, clamped to 0.05..5s"
    )
    p_local_import.add_argument(
        "--yes", action="store_true", help="confirm the user-catalog change"
    )
    p_local_import.set_defaults(func=cmd_local_import)
    p_local_remove = local_sub.add_parser(
        "remove",
        help="remove a provider block previously written by `local import`",
    )
    p_local_remove.add_argument("provider_id", help="managed local provider id")
    p_local_remove.add_argument(
        "--yes", action="store_true", help="confirm the user-catalog change"
    )
    p_local_remove.set_defaults(func=cmd_local_remove)

    p_catalog = sub.add_parser(
        "catalog", help="sync and inspect advisory external provider metadata"
    )
    catalog_sub = p_catalog.add_subparsers(dest="catalog_command", required=True)
    p_catalog_sync = catalog_sub.add_parser(
        "sync", help="sync mnfst/awesome-free-llm-apis metadata into a local cache"
    )
    p_catalog_sync.add_argument(
        "--timeout", type=float, default=20.0, help="download timeout seconds"
    )
    p_catalog_sync.set_defaults(func=cmd_catalog_sync)
    p_catalog_status = catalog_sub.add_parser(
        "status", help="show cached external provider metadata"
    )
    p_catalog_status.add_argument(
        "--limit", type=int, default=10, help="number of providers to show"
    )
    p_catalog_status.set_defaults(func=cmd_catalog_status)

    p_capability = sub.add_parser(
        "capability", help="benchmark-scored model capability for quality routing"
    )
    capability_sub = p_capability.add_subparsers(dest="capability_command", required=True)
    p_cap_sync = capability_sub.add_parser(
        "sync", help="refresh capability scores from public benchmarks (Arena; AA with a key)"
    )
    p_cap_sync.add_argument("--timeout", type=float, default=20.0, help="download timeout seconds")
    p_cap_sync.set_defaults(func=cmd_capability_sync)
    p_cap_status = capability_sub.add_parser(
        "status", help="show capability-score coverage and the top-scoring models"
    )
    p_cap_status.add_argument("--limit", type=int, default=15, help="number of models to show")
    p_cap_status.set_defaults(func=cmd_capability_status)

    p_conformance = sub.add_parser(
        "conformance",
        help="run and inspect deterministic per-model protocol feature canaries",
    )
    conformance_sub = p_conformance.add_subparsers(
        dest="conformance_command",
        required=True,
    )
    p_conf_run = conformance_sub.add_parser(
        "run",
        help="probe a bounded provider/model feature matrix",
    )
    default_features = ",".join(
        (
            FEATURE_CHAT,
            FEATURE_STREAMING,
            FEATURE_TOOLS,
            FEATURE_JSON,
            FEATURE_JSON_SCHEMA,
            FEATURE_VISION,
            FEATURE_RESPONSES,
            FEATURE_ANTHROPIC_MESSAGES,
        )
    )
    p_conf_run.add_argument(
        "--features",
        default=default_features,
        help=f"comma-separated features ({', '.join(FEATURES)})",
    )
    p_conf_run.add_argument("--providers", help="comma-separated configured provider ids")
    p_conf_run.add_argument(
        "--provider",
        help="one exact configured provider id (only with --include-disabled)",
    )
    p_conf_run.add_argument("--model", help="exact model name to probe")
    p_conf_run.add_argument(
        "--include-disabled",
        action="store_true",
        help="maintainer canary for one exact disabled --provider/--model target",
    )
    p_conf_run.add_argument(
        "--all-models",
        action="store_true",
        help="probe every matching enabled model (still bounded by --max-targets)",
    )
    p_conf_run.add_argument(
        "--max-targets",
        type=int,
        choices=range(1, 65),
        default=8,
        metavar="1..64",
        help="maximum exact provider/model targets (default: 8)",
    )
    p_conf_run.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="per-feature timeout seconds, clamped to 0.1..60",
    )
    p_conf_run.add_argument("--json", action="store_true", help="emit sanitized JSON results")
    p_conf_run.set_defaults(func=cmd_conformance_run)
    p_conf_status = conformance_sub.add_parser(
        "status",
        help="show cached per-model feature evidence",
    )
    p_conf_status.add_argument("--json", action="store_true", help="emit the sanitized state JSON")
    p_conf_status.set_defaults(func=cmd_conformance_status)

    p_capacity = sub.add_parser("capacity", help="summarize legitimate LLM capacity")
    capacity_sub = p_capacity.add_subparsers(dest="capacity_command", required=True)
    p_capacity_status = capacity_sub.add_parser(
        "status", help="show provider capacity and quota hints"
    )
    p_capacity_status.add_argument(
        "--target", type=int, default=5, help="desired healthy provider count"
    )
    p_capacity_status.add_argument("--all", action="store_true", help="include missing providers")
    capacity_refresh = p_capacity_status.add_mutually_exclusive_group()
    capacity_refresh.add_argument(
        "--refresh",
        action="store_true",
        help="explicitly refresh the external catalog before showing capacity",
    )
    capacity_refresh.add_argument(
        "--no-catalog-sync",
        action="store_true",
        help="deprecated compatibility alias; cache-first is now the default",
    )
    p_capacity_status.add_argument(
        "--catalog-timeout", type=float, default=8.0, help="external catalog sync timeout seconds"
    )
    p_capacity_status.add_argument(
        "--external-limit", type=int, default=8, help="external-only candidates to show with --all"
    )
    p_capacity_status.set_defaults(func=cmd_capacity_status)

    p_bench = sub.add_parser(
        "benchmark", help="time each configured provider and report latency / success"
    )
    p_bench.add_argument("-m", "--model", help="pin one model name to test on every provider")
    p_bench.add_argument("-p", "--providers", help="comma-separated provider ids to test")
    p_bench.add_argument("--timeout", type=float, default=30.0, help="per-call timeout seconds")
    p_bench.set_defaults(func=cmd_benchmark)

    p_doctor = sub.add_parser("doctor", help="show local diagnostics without calling providers")
    p_doctor.set_defaults(func=cmd_doctor)

    p_proxy = sub.add_parser("proxy", help="run the OpenAI-compatible proxy server")
    p_proxy.add_argument("--host", default="127.0.0.1")
    p_proxy.add_argument("--port", type=int, default=8080)
    p_proxy.add_argument(
        "--api-key",
        default=None,
        help="require this Bearer token on requests (or set FREELLMPOOL_PROXY_KEY)",
    )
    p_proxy.add_argument(
        "--tailnet",
        action="store_true",
        help="alias for `freellmpool tailnet serve` — bind to the local 100.x Tailnet IPv4, require auth",
    )
    p_proxy.add_argument(
        "--allow-lan",
        action="store_true",
        help="allow binding to a non-Tailnet LAN address (still requires auth unless --allow-no-auth)",
    )
    p_proxy.add_argument(
        "--allow-no-auth",
        action="store_true",
        help="explicit escape hatch: serve on a non-loopback bind with NO proxy key",
    )
    p_proxy.set_defaults(func=cmd_proxy)

    p_tailnet = sub.add_parser(
        "tailnet",
        help="Tailnet gateway: serve freellmpool over a Tailscale 100.x address",
    )
    tailnet_sub = p_tailnet.add_subparsers(dest="tailnet_command", required=True)

    p_tailnet_status = tailnet_sub.add_parser(
        "status", help="report Tailscale availability, local Tailnet IPv4, and auth readiness"
    )
    p_tailnet_status.add_argument(
        "--port", type=int, default=8080, help="port to suggest in the next-step hint"
    )
    p_tailnet_status.set_defaults(func=cmd_tailnet_status)

    p_tailnet_serve = tailnet_sub.add_parser(
        "serve", help="bind the proxy to the local Tailnet IPv4 (auth required by default)"
    )
    p_tailnet_serve.add_argument("--port", type=int, default=8080)
    p_tailnet_serve.add_argument(
        "--api-key",
        default=None,
        help="require this Bearer token on requests (or set FREELLMPOOL_PROXY_KEY). "
        "If omitted, a one-session token is generated and printed.",
    )
    p_tailnet_serve.add_argument(
        "--dry-run",
        action="store_true",
        help="print the bind address, auth status, and client setup hints without starting a server",
    )
    p_tailnet_serve.add_argument(
        "--allow-lan",
        action="store_true",
        help="allow binding to a non-Tailnet LAN address (still requires auth unless --allow-no-auth)",
    )
    p_tailnet_serve.add_argument(
        "--allow-no-auth",
        action="store_true",
        help="explicit escape hatch: serve on a non-loopback bind with NO proxy key",
    )
    p_tailnet_serve.set_defaults(func=cmd_tailnet_serve)

    p_tailnet_connect = tailnet_sub.add_parser(
        "connect",
        help="print the OpenAI / Anthropic base URL and env exports for a remote tailnet proxy",
    )
    p_tailnet_connect.add_argument("host", help="tailnet host (MagicDNS name) or 100.x IPv4 of the host running `tailnet serve`")
    p_tailnet_connect.add_argument(
        "--port", type=int, default=8080, help="port the remote proxy is listening on"
    )
    p_tailnet_connect.set_defaults(func=cmd_tailnet_connect)

    p_init = sub.add_parser("init", help="detect environment and print first-run setup plans")
    p_init.add_argument("--yes", action="store_true", help="non-interactive; print a plan")
    p_init.add_argument("--json", action="store_true", help="print machine-readable detection JSON")
    p_init.add_argument("--agent", help="agent profile to set up, e.g. opencode or metaswarm")
    p_init.add_argument(
        "--tailnet",
        action="store_true",
        help="include Tailnet gateway commands and remote client environment",
    )
    p_init.add_argument("--port", type=int, default=8080, help="proxy/tailnet port")
    p_init.add_argument(
        "--force",
        action="store_true",
        help="reserved for future write modes; current init plans are print-only",
    )
    p_init.set_defaults(func=cmd_init)

    p_profile = sub.add_parser(
        "profile", help="list, show, install, and diagnose agent profiles"
    )
    profile_sub = p_profile.add_subparsers(dest="profile_command", required=True)

    p_profile_list = profile_sub.add_parser("list", help="list available profiles")
    p_profile_list.set_defaults(func=cmd_profile_list)

    p_profile_show = profile_sub.add_parser("show", help="show a profile")
    p_profile_show.add_argument("name", help="profile name")
    p_profile_show.set_defaults(func=cmd_profile_show)

    p_profile_install = profile_sub.add_parser(
        "install", help="print copy-pastable setup snippets for a profile"
    )
    p_profile_install.add_argument("name", help="profile name")
    p_profile_install.set_defaults(func=cmd_profile_install)

    p_profile_doctor = profile_sub.add_parser("doctor", help="check a profile setup")
    p_profile_doctor.add_argument("name", help="profile name")
    p_profile_doctor.add_argument(
        "--dry-run",
        action="store_true",
        help="print checks without running binaries or network calls",
    )
    p_profile_doctor.add_argument(
        "--base-url",
        default=None,
        help="override the local proxy base URL for URL checks",
    )
    p_profile_doctor.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="per-check network timeout in seconds",
    )
    p_profile_doctor.set_defaults(func=cmd_profile_doctor)

    p_mcp = sub.add_parser(
        "mcp", help="run an MCP server (stdio) so MCP clients can use free models"
    )
    p_mcp.set_defaults(func=cmd_mcp)

    p_code = sub.add_parser(
        "code", help="wire a coding agent (codex/aider/cline/...) to free models"
    )
    p_code.add_argument("agent", nargs="?", help="agent id (omit to list)")
    p_code.set_defaults(func=cmd_code)

    return parser


def main(argv: list[str] | None = None) -> int:
    from .observe import configure_logging_from_env

    configure_logging_from_env()  # honor FREELLMPOOL_LOG=<level> for the CLI/proxy
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (EOFError, KeyboardInterrupt):
        # No input on a non-TTY/piped stdin (or Ctrl-D/Ctrl-C at a prompt) —
        # exit cleanly instead of dumping a traceback.
        print("\nfreellmpool: cancelled (no input)", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
