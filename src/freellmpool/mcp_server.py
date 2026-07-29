"""A tiny Model Context Protocol (MCP) server, zero extra dependencies.

`freellmpool mcp` speaks MCP over stdio (newline-delimited JSON-RPC 2.0), so an
MCP client — Claude Desktop, Claude Code, Cursor, etc. — can offload subtasks to
free LLMs, get a free *second opinion* from several models at once, see exactly
where a prompt would route, and watch the free tokens add up:

    {
      "mcpServers": {
        "freellmpool": { "command": "freellmpool", "args": ["mcp"] }
      }
    }

Tools exposed:
    free_llm_ask             ask a free model (routing-aware; tells you which model served)
    free_llm_panel           ask N free models in parallel and compare — a free second opinion
    free_llm_second_opinion  same panel behavior, exposed as its own agent-facing tool
    free_llm_battle          bounded multi-model comparison rendered as Markdown
    free_llm_recipe          run a bundled recipe (panel/text) end-to-end
    free_llm_roles           list available roles and recommended use
    free_llm_tailnet_info    safe Tailscale Tailnet connection instructions
    free_llm_quota_wise      local quota-mode / headroom advice (no bypass suggestions)
    tokenmax                 🌈 blast the prompt to a swarm of models; you synthesize them all
    free_llm_route           explain where a prompt WOULD route (difficulty + ranked models), $0
    free_llm_models          list available provider/model ids
    free_llm_quota           today's per-provider usage + daily-limit headroom
    free_llm_stats           lifetime tokens served free + estimated cost avoided

Implemented on the standard library only — no MCP SDK required.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time

from .battle import render_battle_markdown, run_battle
from .config import resolve_alias, split_provider_model
from .mode import current_mode, render_quota_wise_status
from .panel import (
    MAX_PANEL_COUNT,
    clamp_max_tokens,
    clamp_panel_count,
    render_panel_markdown,
    run_panel,
)
from .recipes import (
    MissingRecipeInputError,
    MissingRecipeVariableError,
    RecipeError,
    UnknownRecipeError,
    collect_recipe_input,
    get_recipe,
)
from .roles import format_roles, valid_roles
from .router import Pool
from .routing_modes import PUBLIC_ROUTING_ALIASES, routing_override
from .tailnet import (
    detect_tailnet,
    format_setup_hints,
    generate_session_token_simple,
    safe_base_url,
)
from .task_quality import (
    TASK_GENERAL,
    TASK_HINTS,
    model_task_score,
    task_evidence_table,
    task_resolution,
)
from .tokenmax import HARD_CAP, RAINBOW_BANNER, fan_out, select_targets

_DEFAULT_PROTOCOL = "2025-06-18"
_MAX_PANEL = MAX_PANEL_COUNT
_log = logging.getLogger(__name__)

# Returned in the `initialize` handshake (MCP's standard `instructions` field) so the
# calling agent learns HOW to invoke these tools — chiefly: call them directly instead
# of shelling out to the CLI, which is what hides the live progress + banner from the user.
_SERVER_INSTRUCTIONS = (
    "freellmpool pools many free-tier LLMs behind these tools. Offload self-contained "
    "subtasks (drafting, summarizing, classifying, quick lookups) to free models instead "
    "of spending your own context/quota.\n\n"
    "INVOKE THESE AS MCP TOOLS DIRECTLY. Do NOT shell out to the `freellmpool` CLI (e.g. "
    "spawning `freellmpool mcp` or `freellmpool tokenmax` as a subprocess) to reach them — "
    "that captures the output in your subprocess and hides the live progress, the rainbow "
    "banner, and the answers from the user.\n\n"
    "`tokenmax` streams live `notifications/progress` as each model in the swarm answers "
    "(e.g. `🌈 TOKENMAXXING ▸ 47/168 models…`); call it directly so the client shows that to "
    "the user in real time, and the result carries a rainbow banner plus every answer for YOU "
    "to synthesize. The flashing rainbow ANSI animation can only render on a real terminal "
    "(not inside an MCP chat), so to let the HUMAN watch it pulse, tell them to run "
    '`freellmpool tokenmax "<prompt>"` in their own terminal.'
)

TOOLS = [
    {
        "name": "free_llm_ask",
        "description": (
            "Ask a free LLM (pooled across configured free providers, with automatic failover). "
            "Offload a self-contained subtask — drafting, summarizing, classifying, "
            "brainstorming, a quick lookup — to a free model. The reply tells you which "
            "provider/model actually served it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The question or task."},
                "system": {"type": "string", "description": "Optional system instruction."},
                "model": {
                    "type": "string",
                    "description": "Optional model name or provider/model (e.g. groq/llama-3.3-70b-versatile). Default: auto.",
                },
                "provider": {
                    "type": "string",
                    "description": "Optional provider id to restrict to (e.g. groq, cerebras).",
                },
                "routing": {
                    "type": "string",
                    "enum": list(PUBLIC_ROUTING_ALIASES),
                    "description": "How to pick the model: agent (strongest healthy tier with quota spreading), quality (capability matched to prompt), spread (whole-pool breadth), fast (lowest latency), fair (provider balance), or auto (server default).",
                },
                "task": {
                    "type": "string",
                    "enum": list(TASK_HINTS),
                    "description": "Optional task hint for quality routing; auto classifies locally.",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Max output tokens (default 1024).",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "free_llm_panel",
        "description": (
            "Ask the SAME prompt to several different free models at once and get every "
            "answer back side by side - the agent-facing second-opinion surface. Great "
            "for cross-checking a fact, comparing approaches, or reducing single-model "
            "bias. Optionally have a strong model synthesize the best combined answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The question or task to ask every model.",
                },
                "system": {"type": "string", "description": "Optional system instruction."},
                "n": {
                    "type": "integer",
                    "description": f"How many distinct models to ask (2-{_MAX_PANEL}, default 3).",
                },
                "synthesize": {
                    "type": "boolean",
                    "description": "If true, a quality-routed model synthesizes the panel into one best answer.",
                },
                "routing": {
                    "type": "string",
                    "enum": list(PUBLIC_ROUTING_ALIASES),
                    "description": "How to rank candidate panel models (default: quality).",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Max output tokens per model (default 512).",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "free_llm_second_opinion",
        "description": (
            "Run the shared small-panel second-opinion flow. Same behavior as "
            "`free_llm_panel` — exposed as its own agent-facing tool so callers can "
            "declare their intent (a *second opinion*) without reasoning about the "
            "panel primitive. The panel size is bounded (2-5) and the per-model "
            "token budget is clamped."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The question or task to ask every model.",
                },
                "system": {"type": "string", "description": "Optional system instruction."},
                "n": {
                    "type": "integer",
                    "description": f"How many distinct models to ask (2-{_MAX_PANEL}, default 3).",
                },
                "synthesize": {
                    "type": "boolean",
                    "description": "If true, a quality-routed model synthesizes the panel into one best answer.",
                },
                "routing": {
                    "type": "string",
                    "enum": list(PUBLIC_ROUTING_ALIASES),
                    "description": "How to rank candidate panel models (default: quality).",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Max output tokens per model (default 512).",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "free_llm_battle",
        "description": (
            "Compare a prompt across a small bounded panel of free models and render the "
            "result as a Markdown comparison table. Bounded to a few models (2-5) — for "
            "the every-model stress test use `tokenmax`. Per-model failures stay visible "
            "in the rendered output rather than failing the whole call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The question or task to compare across models.",
                },
                "n": {
                    "type": "integer",
                    "description": f"How many distinct models to ask (2-{_MAX_PANEL}, default 3).",
                },
                "synthesize": {
                    "type": "boolean",
                    "description": "If true, a quality-routed model synthesizes the panel into one best answer.",
                },
                "routing": {
                    "type": "string",
                    "enum": list(PUBLIC_ROUTING_ALIASES),
                    "description": "How to rank candidate panel models (default: quality).",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Max output tokens per model (default 512).",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "free_llm_recipe",
        "description": (
            "Run a bundled recipe end-to-end (e.g. `pr-review`, `second-opinion`, "
            "`repo-summary`). Recipes are bounded, role-driven, and pre-shaped — supply "
            "`name` plus whatever inputs the recipe declares. Missing recipe input or "
            "template variables return a tool error, never a traceback."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Recipe name (e.g. `pr-review`, `second-opinion`, `repo-summary`).",
                },
                "prompt": {
                    "type": "string",
                    "description": "Optional inline text input (used for text-input recipes).",
                },
                "path": {
                    "type": "string",
                    "description": "Optional glob for path-input recipes (e.g. `repo-summary --path 'src/**/*.py'`).",
                },
                "input": {
                    "type": "string",
                    "description": "Optional alias for `prompt` (recipe `input` template variable).",
                },
                "validation_output": {
                    "type": "string",
                    "description": "Optional validation/test output for recipes that template it (e.g. `metaswarm-worker-review`).",
                },
                "opinions": {
                    "type": "integer",
                    "description": f"Panel size for panel-output recipes (2-{_MAX_PANEL}, default 3).",
                },
                "synthesize": {
                    "type": "boolean",
                    "description": "If true, a quality-routed model synthesizes the panel into one best answer.",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Max output tokens (default 1024 for text recipes; panel recipes honor recipe defaults).",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "free_llm_roles",
        "description": (
            "List bundled ask-role presets (e.g. `coder`, `critic`, `summarizer`, "
            "`second-opinion`) with their routing mode, max-tokens, temperature, and "
            "recommended use. Pass `name` to get the full details for one role."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Optional role name to fetch a single role's details.",
                },
            },
        },
    },
    {
        "name": "free_llm_tailnet_info",
        "description": (
            "Show safe Tailscale Tailnet connection instructions for serving "
            "`freellmpool proxy` on another machine. Includes the local Tailnet IPv4, "
            "status, and OpenAI / Anthropic client env-var hints. Output NEVER contains "
            "a real local bearer token — it uses a `<proxy-key>` placeholder — and never "
            "leaks provider API keys. Degrades cleanly when `tailscale` is absent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {
                    "type": "integer",
                    "description": "Optional proxy port to include in setup hints (default 8080).",
                },
            },
        },
    },
    {
        "name": "free_llm_quota_wise",
        "description": (
            "Show local quota-wise status and headroom advice built from your locally "
            "tracked counters. Active mode, recommended mode, and per-provider used / "
            "limit / remaining. Output NEVER recommends account rotation or rate-limit "
            "bypass — it only suggests waiting for reset, lowering fan-out or token "
            "budget, or making an explicit paid choice outside the default flow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "tokenmax",
        "description": (
            "🌈 TOKENMAX 🌈 — gloriously excessive: fan the SAME prompt out to EVERY available "
            "model across EVERY configured provider at once (a deliberate maximum-effort stress "
            "test), then YOU (the calling model) synthesize the single best answer from all of "
            "them. Maximum free tokens, maximum cross-checking. Tongue-in-cheek, but genuinely "
            "useful for the hardest questions where you want every model's take. "
            "Call this tool DIRECTLY (your client receives live `🌈 TOKENMAXXING ▸ N/total` "
            "progress as each model answers) — do NOT shell out to the CLI, which hides that "
            "from the user. To let the human watch the flashing rainbow, suggest they run "
            '`freellmpool tokenmax "<prompt>"` in their own terminal.'
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The prompt to blast to every model."},
                "system": {"type": "string", "description": "Optional system instruction."},
                "max_models": {
                    "type": "integer",
                    "description": f"Optional cap on how many models to hit (default: ALL of them; hard max {HARD_CAP}).",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Max output tokens per model (default 400).",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "free_llm_route",
        "description": (
            "Explain where a prompt WOULD be routed without spending a single token: the "
            "estimated difficulty and the ranked list of candidate models (with capability "
            "scores) for the chosen routing mode. Use it to understand or debug routing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The prompt to analyze."},
                "routing": {
                    "type": "string",
                    "enum": list(PUBLIC_ROUTING_ALIASES),
                    "description": "Routing mode to explain (default: the server's mode).",
                },
                "task": {
                    "type": "string",
                    "enum": list(TASK_HINTS),
                    "description": "Optional task hint to explain.",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "free_llm_models",
        "description": "List the available free provider/model ids freellmpool can route to.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "free_llm_quota",
        "description": (
            "Show today's free-tier usage (UTC): per-provider request counts and "
            "daily-limit headroom, plus session totals and estimated cost avoided."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "free_llm_stats",
        "description": (
            "Show freellmpool's LIFETIME totals (persisted across restarts): tokens served "
            "free, requests, and estimated cost avoided vs Claude Opus 4.8 — the number that keeps growing."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _result(mid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _text(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _routing_arg(value) -> str | None:
    """Map the tool's routing arg to a pool routing override (auto/unknown -> None)."""
    return routing_override(value)


def _messages(system, prompt: str) -> list[dict[str, str]]:
    msgs: list[dict[str, str]] = []
    if isinstance(system, str) and system.strip():
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return msgs


def _clamp_int(value, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _max_tokens(value, default: int) -> int:
    return _clamp_int(value, default, 1, 8192)


def _resolve_model(model, env, provider_ids=None) -> tuple[list[str] | None, str | None]:
    """Resolve a model arg to (providers, model) filters, honoring aliases. Only splits a
    ``provider/model`` prefix when it's a real provider id, so slash-bearing model names
    (HF / OpenRouter / Kilo ids) aren't mis-split."""
    if not (isinstance(model, str) and model):
        return None, None
    model = resolve_alias(model, env)
    if model == "auto":
        return None, None
    return split_provider_model(model, provider_ids)


def _call_tool(pool: Pool, params: dict, notify=None) -> dict:
    name = params.get("name")
    args = params.get("arguments") or {}
    if name == "free_llm_ask":
        return _tool_ask(pool, args)
    if name == "free_llm_panel":
        return _tool_panel(pool, args)
    if name == "free_llm_second_opinion":
        return _tool_second_opinion(pool, args)
    if name == "free_llm_battle":
        return _tool_battle(pool, args)
    if name == "free_llm_recipe":
        return _tool_recipe(pool, args)
    if name == "free_llm_roles":
        return _tool_roles(args)
    if name == "free_llm_tailnet_info":
        return _tool_tailnet_info(args)
    if name == "free_llm_quota_wise":
        return _tool_quota_wise(pool)
    if name == "tokenmax":
        return _tool_tokenmax(pool, args, notify=notify)
    if name == "free_llm_route":
        return _tool_route(pool, args)
    if name == "free_llm_models":
        ids = [f"{p.id}/{m.name}" for p in pool.providers for m in p.models if m.enabled]
        return _text("\n".join(ids) or "no providers configured")
    if name == "free_llm_quota":
        return _text(_quota_summary(pool))
    if name == "free_llm_stats":
        return _text(_lifetime_summary(pool))
    return _text(f"unknown tool: {name}", is_error=True)


def _tool_ask(pool: Pool, args: dict) -> dict:
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _text("'prompt' is required", is_error=True)
    provider = args.get("provider")
    providers = [provider] if provider else None
    p_filter, model = _resolve_model(args.get("model"), pool.env, {p.id for p in pool.providers})
    if p_filter is not None:
        providers = p_filter
    routing = _routing_arg(args.get("routing"))
    started = time.monotonic()
    try:
        reply = pool.chat(
            _messages(args.get("system"), prompt),
            model=model,
            providers=providers,
            routing=routing,
            max_tokens=_max_tokens(args.get("max_tokens"), 1024),
            task=args.get("task"),
        )
    except Exception as exc:  # noqa: BLE001 — surface as a tool error
        return _text(f"{type(exc).__name__}: {exc}", is_error=True)
    ms = round((time.monotonic() - started) * 1000)
    tag = "cache" if reply.cached else f"{ms}ms"
    return _text(f"{reply.text}\n\n— via {reply.provider_id}/{reply.model} ({tag})")


def _tool_panel(pool: Pool, args: dict) -> dict:
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _text("'prompt' is required", is_error=True)
    routing = _routing_arg(args.get("routing")) or "quality"
    result = run_panel(
        pool,
        prompt=prompt.strip(),
        system=args.get("system"),
        n=clamp_panel_count(args.get("n")),
        routing=routing,
        max_tokens=clamp_max_tokens(args.get("max_tokens")),
        synthesize=bool(args.get("synthesize")),
    )
    if not result.answers:
        return _text("no providers configured", is_error=True)
    return _text(render_panel_markdown(result))


# `_tool_second_opinion` is the same callable as `_tool_panel` so the
# "free_llm_second_opinion just runs the panel" contract is enforced by
# Python identity, not just by convention — any future change to the panel
# behavior automatically reaches both tools, and dispatch in `_call_tool`
# routes the second-opinion tool name straight to the panel handler.
_tool_second_opinion = _tool_panel


def _tool_battle(pool: Pool, args: dict) -> dict:
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _text("'prompt' is required", is_error=True)
    routing = _routing_arg(args.get("routing")) or "quality"
    result = run_battle(
        pool,
        prompt=prompt.strip(),
        n=clamp_panel_count(args.get("n")),
        routing=routing,
        max_tokens=clamp_max_tokens(args.get("max_tokens")),
        synthesize=bool(args.get("synthesize")),
    )
    if not result.answers:
        return _text("no providers configured", is_error=True)
    return _text(render_battle_markdown(result))


def _tool_recipe(pool: Pool, args: dict) -> dict:
    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        return _text("'name' is required", is_error=True)
    try:
        recipe = get_recipe(name.strip())
    except UnknownRecipeError as exc:
        return _text(f"{type(exc).__name__}: {exc}", is_error=True)
    except RecipeError as exc:  # unknown schema / malformed JSON
        return _text(f"{type(exc).__name__}: {exc}", is_error=True)

    # Inline prompt (or `input`) is the recipe's `input` template variable.
    # `validation_output` is optional and only used by recipes that declare it.
    inline_prompt = args.get("input")
    if inline_prompt is None:
        inline_prompt = args.get("prompt") or ""
    inline_prompt = inline_prompt if isinstance(inline_prompt, str) else ""
    validation_output = args.get("validation_output") or ""
    if not isinstance(validation_output, str):
        validation_output = ""

    path_arg = args.get("path")
    if path_arg is not None and not isinstance(path_arg, str):
        return _text("'path' must be a string", is_error=True)

    try:
        input_text, path_used = collect_recipe_input(
            recipe,
            prompt=inline_prompt,
            stdin="",
            input_file=None,
            path=path_arg,
        )
    except MissingRecipeInputError as exc:
        return _text(f"{type(exc).__name__}: {exc}", is_error=True)

    variables = {
        "input": input_text,
        "path": path_used or "",
        "validation_output": validation_output,
    }

    # Fail-fast: surface missing-variable errors before any fan-out work.
    # run_recipe would raise the same MissingRecipeVariableError itself, but
    # doing the render_prompt check here keeps that contract explicit at the
    # MCP boundary and returns a clean tool error in the same code path.
    from .recipes import render_prompt, run_recipe

    try:
        render_prompt(recipe, variables)
    except MissingRecipeVariableError as exc:
        return _text(f"{type(exc).__name__}: {exc}", is_error=True)

    opinions = clamp_panel_count(args.get("opinions")) if args.get("opinions") is not None else 3
    max_tokens_arg = args.get("max_tokens")
    if isinstance(max_tokens_arg, int) and max_tokens_arg > 0:
        max_tokens = max_tokens_arg
    else:
        max_tokens = 1024

    try:
        run = run_recipe(
            pool,
            recipe,
            input_text=input_text,
            path=path_used,
            validation_output=validation_output,
            opinions=opinions,
            synthesize=bool(args.get("synthesize")),
            max_tokens=max_tokens,
        )
    except MissingRecipeVariableError as exc:
        return _text(f"{type(exc).__name__}: {exc}", is_error=True)
    except Exception as exc:  # noqa: BLE001 - surface as a tool error, not a traceback
        return _text(f"{type(exc).__name__}: {exc}", is_error=True)

    header = f"{recipe.name} ({recipe.version}) — {recipe.description}"
    footer = (
        f"\n\n— recipe `{recipe.name}` (role: {recipe.role})"
        if run.provider_id is None
        else f"\n\n— via {run.provider_id}/{run.model}"
    )
    return _text(header + "\n\n" + run.output + footer)


def _tool_roles(args: dict) -> dict:
    name = args.get("name")
    if isinstance(name, str) and name.strip():
        from .roles import get_role

        role = get_role(name.strip())
        if role is None:
            known = ", ".join(valid_roles())
            return _text(f"unknown role '{name}'. Known: {known}", is_error=True)
        extras: list[str] = []
        if role.routing is not None:
            extras.append(f"routing={role.routing}")
        else:
            extras.append("routing=pool default")
        if role.max_tokens is not None:
            extras.append(f"max_tokens={role.max_tokens}")
        if role.temperature is not None:
            extras.append(f"temperature={role.temperature}")
        return _text(
            f"{role.name} — {role.description} ({', '.join(extras)})"
            + (f"\n  system_prefix: {role.system_prefix}" if role.system_prefix else "")
        )
    return _text(format_roles())


def _tool_tailnet_info(args: dict) -> dict:
    port = args.get("port")
    if port is None:
        port = 8080
    if not isinstance(port, int) or not (1 <= port <= 65535):
        return _text("'port' must be an integer in 1..65535", is_error=True)

    status = detect_tailnet()
    # Build a safe status line + setup hints without ever embedding the
    # user's real bearer token. The hints block uses a `<proxy-key>`
    # placeholder; the actual token is printed by the proxy itself.
    placeholder_token = generate_session_token_simple(8)  # tiny, never real

    lines: list[str] = []
    if status.usable:
        base = safe_base_url(status.ipv4 or "127.0.0.1", port)
        lines.append(f"Tailnet: {status.state} ({status.ipv4})")
        lines.append("")
        lines.append("Serve on this host:")
        lines.append(f"  freellmpool proxy --host {status.ipv4} --port {port}")
        lines.append("")
        lines.append("Client setup on the other Tailnet machine:")
        lines.append(format_setup_hints(base_url=base, token=placeholder_token, token_label="<proxy-key>"))
        lines.append("")
        lines.append(
            "(Token placeholder only — freellmpool proxy prints the real token "
            "to its own console when it boots; do not paste provider keys here.)"
        )
    else:
        # Degraded path: CLI missing / logged out / no IPv4 / malformed.
        lines.append(f"Tailnet: {status.state}")
        if status.detail:
            lines.append(f"  detail: {status.detail}")
        lines.append("")
        lines.append(
            "freellmpool will still serve on loopback for local-only use. "
            "Install / log into Tailscale to share the proxy across your Tailnet."
        )
    return _text("\n".join(lines))


def _tool_quota_wise(pool: Pool) -> dict:
    snapshot = pool.quota.snapshot()
    active = current_mode(pool.env) == "wise"
    body = render_quota_wise_status(pool.providers, snapshot, active=active)

    # Refuse to surface any wording that hints at account rotation / bypass /
    # automatic paid fallback. The advisory lines below are the only acceptable
    # set per the task contract.
    advice = [
        "",
        "advice (local counters only):",
        "  - if remaining is 0 on every declared-RPD provider, wait for the UTC reset",
        "  - lower fan-out (smaller panel / lower --max-tokens / smaller --max-models)",
        "  - if you really need more throughput, make an explicit paid choice outside the default flow",
        "",
        "this tool only reports local counters and never suggests workarounds",
        "such as switching accounts, evading provider limits, or implicit",
        "fallback to paid tiers. Counts are advisory only.",
    ]
    return _text(body + "\n" + "\n".join(advice))


def _tool_tokenmax(pool: Pool, args: dict, notify=None) -> dict:
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _text("'prompt' is required", is_error=True)
    max_tokens = _max_tokens(args.get("max_tokens"), 350)
    msgs = _messages(args.get("system"), prompt)
    picks, n_providers = select_targets(pool, msgs, args.get("max_models"))
    if not picks:
        return _text("no providers configured", is_error=True)

    # Live progress for hosts that support it (Claude Code shows the message ticking up).
    # This is the ONLY "it's alive" signal that reaches an MCP user: raw ANSI can't animate
    # inside an MCP chat, so there is no rainbow throb here (it would only spew breadcrumbs
    # into the host's stderr log). For the genuine flashing animation use the CLI
    # (`freellmpool tokenmax`); for a live in-harness graphic use the OpenCode TUI plugin.
    def progress(done: int, total: int, _label: str) -> None:
        if notify is not None:
            notify(done, total, f"🌈 TOKENMAXXING ▸ {done}/{total} models")

    answered, failed = fan_out(pool, msgs, picks, max_tokens=max_tokens, progress=progress)

    head = [
        f"{RAINBOW_BANNER} TOKENMAX — blasted your prompt to {len(picks)} models across "
        f"{n_providers} providers; {len(answered)} answered, {len(failed)} unavailable. "
        f"{RAINBOW_BANNER}",
        "Synthesize the single best, correct answer from every response below "
        "(weigh agreement, discard outliers):",
        "",
    ]
    body = [f"### {lbl}\n{txt}\n" for lbl, txt in answered]
    if failed:
        shown = ", ".join(failed[:30]) + ("…" if len(failed) > 30 else "")
        body.append(f"_{len(failed)} unavailable (rate-limited / errored): {shown}_")
    return _text("\n".join(head + body))


def _tool_route(pool: Pool, args: dict) -> dict:
    from .capability import capability_table, model_capability, prompt_difficulty

    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _text("'prompt' is required", is_error=True)
    routing = _routing_arg(args.get("routing")) or pool.routing
    msgs = _messages(None, prompt)
    difficulty = prompt_difficulty(msgs)
    try:
        resolution = task_resolution(msgs, args.get("task"))
        targets = pool.rank_targets(msgs, routing=routing, task=resolution.task)
    except ValueError as exc:
        return _text(str(exc), is_error=True)
    table = capability_table()
    task_table = (
        task_evidence_table(resolution.task)
        if resolution.task != TASK_GENERAL
        else {}
    )
    lines = [
        f"routing mode: {routing}",
        f"estimated prompt difficulty: {difficulty:.2f}  (0 = trivial, 1 = hardest)",
        f"resolved task: {resolution.task} ({resolution.source})",
        "",
        f"top candidates (in failover order){' — strongest-tier first' if routing == 'agent' else ' — strongest-fit first' if routing == 'quality' else ''}:",
    ]
    for i, t in enumerate(targets[:8], 1):
        cap = model_capability(t.model, table)
        task_score = model_task_score(t.model, task_table)
        task_text = (
            ""
            if resolution.task == TASK_GENERAL
            else f", task evidence {task_score:.2f}"
            if task_score is not None
            else ", task evidence unmeasured"
        )
        lines.append(
            f"  {i:>2}. {t.provider.id}/{t.model}  "
            f"(capability {cap:.2f}{task_text})"
        )
    if not targets:
        lines.append("  (no configured candidates)")
    return _text("\n".join(lines))


def _quota_summary(pool: Pool) -> str:
    from .savings import usd_saved

    snap = pool.quota.snapshot()  # {provider::model: count} for today (UTC)
    used: dict[str, int] = {}
    for key, count in snap.items():
        pid = key.split("::", 1)[0]
        used[pid] = used.get(pid, 0) + count
    # per-provider daily-limit hint = max rpd across its models (0 = unmetered)
    limit: dict[str, int] = {}
    for p in pool.providers:
        rpds = [m.rpd for m in p.models if m.rpd > 0]
        limit[p.id] = max(rpds) if rpds else 0

    lines = ["Today's free-tier usage (UTC):", ""]
    lines.append(f"{'provider':<13}{'used':>6}  {'daily limit/model':<18}remaining")
    for p in pool.providers:
        u = used.get(p.id, 0)
        lim = limit[p.id]
        if lim:
            lines.append(f"{p.id:<13}{u:>6}  ~{lim:<17}{max(0, lim - u)}")
        else:
            lines.append(f"{p.id:<13}{u:>6}  {'unmetered':<18}-")

    s = pool.stats_snapshot()
    lines += [
        "",
        f"session: {s.get('requests', 0)} requests, {s.get('cache_hits', 0)} cache hits, "
        f"{s.get('completion_tokens', 0)} output tokens",
        f"estimated cost avoided vs Claude Opus 4.8: ~${usd_saved(s.get('prompt_tokens'), s.get('completion_tokens')):.4f}",
    ]
    return "\n".join(lines)


def _lifetime_summary(pool: Pool) -> str:
    from .savings import usd_saved

    life = pool.lifetime_stats()
    tokens = int(life.get("prompt_tokens", 0)) + int(life.get("completion_tokens", 0))
    saved = usd_saved(life.get("prompt_tokens"), life.get("completion_tokens"))
    lines = [
        "freellmpool — served free (lifetime):",
        f"  requests:   {life.get('requests', 0):,}",
        f"  tokens:     {tokens:,}",
        f"  cache hits: {life.get('cache_hits', 0):,}",
        f"  estimated cost avoided vs Claude Opus 4.8: ~${saved:,.2f}"
        if saved >= 1
        else f"  estimated cost avoided vs Claude Opus 4.8: ~${saved:.4f}",
    ]
    if life.get("first_seen"):
        lines.append(f"  since: {life['first_seen']}")
    return "\n".join(lines)


def _make_notify(params: dict, send_notification):
    """Build a progress callback that emits MCP `notifications/progress`, but only
    when the client supplied a progressToken (per the MCP spec) and we have a
    channel to send on. Otherwise return None so the tool runs silently."""
    if send_notification is None:
        return None
    token = (params.get("_meta") or {}).get("progressToken")
    if token is None:
        return None

    def notify(progress: int, total: int, message: str) -> None:
        send_notification(
            {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {
                    "progressToken": token,
                    "progress": progress,
                    "total": total,
                    "message": message,
                },
            }
        )

    return notify


def handle_message(
    pool: Pool, msg: dict, *, version: str = "0.0.0", send_notification=None
) -> dict | None:
    """Handle one JSON-RPC message. Returns a response dict, or None for
    notifications (which get no reply). `send_notification`, if given, is a
    callback the server can use to emit out-of-band notifications (e.g. progress)
    while a tool is still running."""
    if not isinstance(msg, dict):
        return _error(None, -32600, "invalid request: not a JSON-RPC object")
    if "method" not in msg or not isinstance(msg["method"], str):
        # A request (has id) without a valid method is an invalid request; a
        # notification (no id) we simply drop.
        return _error(msg["id"], -32600, "invalid request: missing method") if "id" in msg else None
    method = msg["method"]
    if "id" not in msg:  # notification (e.g. notifications/initialized)
        return None
    mid = msg["id"]
    try:
        if method == "initialize":
            params = msg.get("params") or {}
            protocol = params.get("protocolVersion") or _DEFAULT_PROTOCOL
            return _result(
                mid,
                {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "freellmpool", "version": version},
                    "instructions": _SERVER_INSTRUCTIONS,
                },
            )
        if method == "ping":
            return _result(mid, {})
        if method == "tools/list":
            return _result(mid, {"tools": TOOLS})
        if method == "tools/call":
            params = msg.get("params") or {}
            notify = _make_notify(params, send_notification)
            return _result(mid, _call_tool(pool, params, notify=notify))
        return _error(mid, -32601, f"method not found: {method}")
    except Exception:  # noqa: BLE001 — never crash the loop
        _log.exception("unexpected MCP request failure")
        return _error(mid, -32603, "internal error")


def serve_stdio(pool: Pool, version: str = "0.0.0") -> None:
    """Run the MCP server over stdio until stdin closes."""
    out = sys.stdout
    # A lock guards every write so progress notifications emitted from tokenmax's
    # worker threads can't interleave mid-line with the final response.
    #
    # Deadlock-safety invariant: the lock is only ever held for the duration of a
    # single write_obj() call. handle_message() (which runs the tool's fan-out and
    # all of its worker-thread progress notifications) is fully evaluated BEFORE
    # emit()/write_obj() acquires the lock — so the main thread never holds the lock
    # while workers are trying to acquire it. Do not move write_obj() to wrap a
    # handle_message() call, or the workers' notifications would deadlock.
    write_lock = threading.Lock()

    def write_obj(obj) -> None:
        with write_lock:
            out.write(json.dumps(obj) + "\n")
            out.flush()

    def emit(resp) -> None:
        if resp is not None:
            write_obj(resp)

    def send_notification(obj) -> None:
        try:  # best-effort; a failed progress ping must never abort the tool
            write_obj(obj)
        except Exception:  # noqa: BLE001
            pass

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                emit(_error(None, -32700, "parse error: invalid JSON"))
                continue
            if isinstance(msg, list):  # JSON-RPC batch
                if not msg:
                    emit(_error(None, -32600, "invalid request: empty batch"))
                    continue
                responses = [
                    r
                    for r in (
                        handle_message(
                            pool, m, version=version, send_notification=send_notification
                        )
                        for m in msg
                    )
                    if r
                ]
                # JSON-RPC 2.0: a batch gets a single response that is an array of the
                # individual responses (omitting notifications). All-notifications → no reply.
                if responses:
                    write_obj(responses)
                continue
            emit(handle_message(pool, msg, version=version, send_notification=send_notification))
    finally:
        pool.quota.flush()
