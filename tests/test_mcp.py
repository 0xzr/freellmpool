"""MCP server: JSON-RPC message handling (no real stdio needed)."""

from __future__ import annotations

import pytest
from helpers import make_post, openai_body

from freellmpool.mcp_server import handle_message
from freellmpool.models import Reply
from freellmpool.router import Pool


def _pool(providers, env, quota, post=None):
    return Pool(providers, quota=quota, env=env, post=post or make_post({}))


def test_initialize(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        version="0.6.0",
    )
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "freellmpool"
    assert resp["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in resp["result"]["capabilities"]
    # The handshake teaches agents to invoke tools directly (not via the CLI), which is
    # what lets tokenmax's live progress reach the user instead of a hidden subprocess.
    instructions = resp["result"]["instructions"]
    assert "tokenmax" in instructions
    assert "directly" in instructions.lower()
    assert "freellmpool tokenmax" in instructions  # how the human sees the flashing animation


def test_notification_gets_no_reply(providers, env, quota):
    pool = _pool(providers, env, quota)
    assert handle_message(pool, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(pool, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {
        "free_llm_ask",
        "free_llm_panel",
        "free_llm_second_opinion",
        "free_llm_battle",
        "free_llm_recipe",
        "free_llm_roles",
        "free_llm_tailnet_info",
        "free_llm_quota_wise",
        "tokenmax",
        "free_llm_route",
        "free_llm_models",
        "free_llm_quota",
        "free_llm_stats",
    }


# Tools MUST NOT expose mutating policy knobs (e.g. a set_policy tool).
@pytest.mark.parametrize("forbidden_name", ["set_policy", "set_mode", "set_routing"])
def test_tools_list_has_no_mutating_policy_tool(providers, env, quota, forbidden_name):
    pool = _pool(providers, env, quota)
    resp = handle_message(pool, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert forbidden_name not in names


def test_tool_schemas_expose_expected_fields(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(pool, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    by_name = {t["name"]: t for t in resp["result"]["tools"]}
    ask_props = by_name["free_llm_ask"]["inputSchema"]["properties"]
    assert ask_props["task"]["enum"] == ["auto", "general", "grounded-reading"]
    route_props = by_name["free_llm_route"]["inputSchema"]["properties"]
    assert route_props["task"]["enum"] == ["auto", "general", "grounded-reading"]

    # Panel-style tools share the same shape (n clamp 2-5, max_tokens, synthesize, routing enum).
    for tool_name in ("free_llm_panel", "free_llm_second_opinion", "free_llm_battle"):
        schema = by_name[tool_name]["inputSchema"]
        assert "prompt" in schema["required"]
        props = schema["properties"]
        assert props["n"]["type"] == "integer"
        assert "synthesize" in props
        assert props["routing"]["enum"] == ["auto", "agent", "spread", "fast", "quality", "fair"]
        assert props["max_tokens"]["type"] == "integer"

    # Recipe: bounded argument shape, no duplicate dispatch logic.
    recipe_schema = by_name["free_llm_recipe"]["inputSchema"]
    assert recipe_schema["required"] == ["name"]
    assert {"name", "prompt", "path", "input", "validation_output", "opinions", "synthesize", "max_tokens"} <= set(
        recipe_schema["properties"]
    )

    # Roles: optional single-name filter.
    roles_schema = by_name["free_llm_roles"]["inputSchema"]
    assert "name" in roles_schema["properties"]

    # Tailnet info: optional port.
    tailnet_schema = by_name["free_llm_tailnet_info"]["inputSchema"]
    assert tailnet_schema["properties"]["port"]["type"] == "integer"

    # Quota-wise: no required args, no provider key fields.
    quota_wise_schema = by_name["free_llm_quota_wise"]["inputSchema"]
    assert quota_wise_schema.get("required", []) == []
    quota_wise_props = " ".join(quota_wise_schema["properties"].keys())
    assert "api_key" not in quota_wise_props.lower()
    assert "bearer" not in quota_wise_props.lower()


def test_tools_call_quota(providers, env, quota):
    pool = _pool(providers, env, quota)
    pool.ask("hi")  # record some usage
    resp = handle_message(
        pool,
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "free_llm_quota"}},
    )
    text = resp["result"]["content"][0]["text"]
    assert "usage" in text.lower()
    assert "session:" in text


def test_tools_call_ask(providers, env, quota):
    pool = _pool(providers, env, quota, post=make_post({}))  # returns "ok"
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "free_llm_ask", "arguments": {"prompt": "hi"}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert text.startswith("ok")
    assert "via alpha/" in text  # provenance footer names the serving model
    assert resp["result"]["isError"] is False


def test_tools_call_ask_forwards_task_hint(providers, env, quota, monkeypatch):
    pool = _pool(providers, env, quota)
    captured = {}

    def fake_chat(messages, **kwargs):
        captured.update(kwargs)
        return Reply(text="ok", provider_id="alpha", model="alpha-small", raw={})

    monkeypatch.setattr(pool, "chat", fake_chat)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 31,
            "method": "tools/call",
            "params": {
                "name": "free_llm_ask",
                "arguments": {"prompt": "read this", "task": "grounded-reading"},
            },
        },
    )

    assert resp["result"]["isError"] is False
    assert captured["task"] == "grounded-reading"


def test_tools_call_panel(providers, env, quota):
    pool = _pool(providers, env, quota)  # all providers return "ok"
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "free_llm_panel", "arguments": {"prompt": "hi", "n": 2}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert "panel" in text.lower()
    assert text.count("###") >= 2  # one section per model asked


def test_tools_call_panel_defaults_to_three_models(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 18,
            "method": "tools/call",
            "params": {"name": "free_llm_panel", "arguments": {"prompt": "hi"}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert text.count("###") == 3


def test_tools_call_panel_clamps_model_count(env, quota):
    from freellmpool.models import Model, Provider

    providers = [
        Provider(
            id=f"p{i}",
            label=f"P{i}",
            adapter="openai",
            base_url=f"https://p{i}.test/v1",
            auth="none",
            models=(Model(f"mistral-{i}-7b"),),
        )
        for i in range(6)
    ]
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 19,
            "method": "tools/call",
            "params": {"name": "free_llm_panel", "arguments": {"prompt": "hi", "n": 99}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert text.count("###") == 5


def test_tools_call_panel_synthesis_failure_is_nonfatal(env, quota):
    from freellmpool.models import Model, Provider

    providers = [
        Provider(
            id="alpha",
            label="Alpha",
            adapter="openai",
            base_url="https://alpha.test/v1",
            auth="none",
            models=(Model("llama-3.1-8b"),),
        ),
        Provider(
            id="beta",
            label="Beta",
            adapter="openai",
            base_url="https://beta.test/v1",
            auth="none",
            models=(Model("qwen3-32b"),),
        ),
    ]

    def responder(url, headers, body):
        if "Synthesize the single" in body["messages"][0]["content"]:
            return 500, {"error": "synthesis down"}
        return 200, openai_body("ok")

    pool = _pool(providers, env, quota, post=make_post({"test": responder}))
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 20,
            "method": "tools/call",
            "params": {
                "name": "free_llm_panel",
                "arguments": {"prompt": "hi", "n": 2, "synthesize": True},
            },
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert resp["result"]["isError"] is False
    assert "synthesis (failed)" in text
    assert text.count("###") == 3


def test_tools_call_tokenmax(providers, env, quota):
    pool = _pool(providers, env, quota)  # all providers return "ok"
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {"name": "tokenmax", "arguments": {"prompt": "hi", "max_models": 3}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert "TOKENMAX" in text
    assert "synthesize" in text.lower()  # the caller is told to synthesize
    assert text.count("###") >= 1  # at least one model's answer included


def test_tokenmax_default_respects_hard_cap(providers, env, quota, monkeypatch):
    import freellmpool.tokenmax as TM

    monkeypatch.setattr(TM, "HARD_CAP", 2)  # even "all" must obey the ceiling
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 14,
            "method": "tools/call",
            "params": {"name": "tokenmax", "arguments": {"prompt": "hi"}},  # no max_models -> ALL
        },
    )
    assert "to 2 models" in resp["result"]["content"][0]["text"]


def test_tokenmax_result_has_rainbow_banner(providers, env, quota):
    from freellmpool.tokenmax import RAINBOW_BANNER

    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 15,
            "method": "tools/call",
            "params": {"name": "tokenmax", "arguments": {"prompt": "hi"}},
        },
    )
    assert RAINBOW_BANNER in resp["result"]["content"][0]["text"]  # color lands in every host


def test_tokenmax_emits_progress_when_token_present(providers, env, quota):
    pool = _pool(providers, env, quota)
    sent: list = []
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 16,
            "method": "tools/call",
            "params": {
                "name": "tokenmax",
                "arguments": {"prompt": "hi"},
                "_meta": {"progressToken": "tok-1"},
            },
        },
        send_notification=sent.append,
    )
    assert resp["result"]["isError"] is False
    progs = [m for m in sent if m.get("method") == "notifications/progress"]
    assert progs, "expected progress notifications when a progressToken is supplied"
    last = progs[-1]["params"]
    assert last["progressToken"] == "tok-1"
    assert last["progress"] == last["total"]  # the final tick reaches 100%
    assert "TOKENMAXXING" in last["message"]


def test_tokenmax_silent_without_progress_token(providers, env, quota):
    pool = _pool(providers, env, quota)
    sent: list = []
    handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 17,
            "method": "tools/call",
            "params": {"name": "tokenmax", "arguments": {"prompt": "hi"}},  # no _meta
        },
        send_notification=sent.append,
    )
    assert not [m for m in sent if m.get("method") == "notifications/progress"]


def test_tools_call_route_is_zero_token(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "free_llm_route",
                "arguments": {"prompt": "hi", "routing": "quality"},
            },
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert "difficulty" in text.lower()
    assert "resolved task: general (auto)" in text.lower()
    assert "alpha/" in text  # a ranked candidate
    assert pool.stats_snapshot()["requests"] == 0  # explained without spending a token


def test_tools_call_stats(providers, env, quota):
    pool = _pool(providers, env, quota)
    pool.ask("hi")  # record some usage
    resp = handle_message(
        pool,
        {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "free_llm_stats"}},
    )
    text = resp["result"]["content"][0]["text"]
    assert "lifetime" in text.lower()
    assert "Claude Opus 4.8" in text


def test_tools_call_ask_missing_prompt(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "free_llm_ask", "arguments": {}},
        },
    )
    assert resp["result"]["isError"] is True


def test_tools_call_models(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "free_llm_models"}},
    )
    text = resp["result"]["content"][0]["text"]
    assert "alpha/alpha-small" in text


def test_unknown_method_errors(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(pool, {"jsonrpc": "2.0", "id": 6, "method": "bogus/method"})
    assert resp["error"]["code"] == -32601


def test_unexpected_dispatch_error_is_redacted_and_logged(
    providers, env, quota, monkeypatch, caplog
):
    from freellmpool import mcp_server

    secret = "sentinel-secret-value"

    def boom(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(mcp_server, "_call_tool", boom)
    pool = _pool(providers, env, quota)
    with caplog.at_level("ERROR", logger="freellmpool.mcp_server"):
        resp = handle_message(
            pool,
            {
                "jsonrpc": "2.0",
                "id": 99,
                "method": "tools/call",
                "params": {"name": "free_llm_models"},
            },
        )

    assert resp["error"]["code"] == -32603
    assert resp["error"]["message"] == "internal error"
    assert secret not in str(resp)
    assert secret in caplog.text


def test_ask_failover_in_tool(providers, env, quota):
    post = make_post({"alpha.test": (500, {}), "beta.test": (200, openai_body("from beta"))})
    pool = _pool(providers, env, quota, post=post)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "free_llm_ask", "arguments": {"prompt": "hi", "provider": "alpha"}},
        },
    )
    # alpha 500s and there's no beta in provider filter → tool error surfaced
    assert resp["result"]["isError"] is True


def test_parse_error_returns_neg32700():
    # serve_stdio emits a JSON-RPC parse error for invalid JSON
    import io

    from freellmpool.mcp_server import serve_stdio

    out = io.StringIO()
    import sys

    old = sys.stdout
    sys.stdin_backup = sys.stdin
    sys.stdin = io.StringIO("{ not json\n")
    sys.stdout = out
    try:
        from freellmpool.router import Pool

        serve_stdio(Pool([], env={}))
    finally:
        sys.stdout = old
        sys.stdin = sys.stdin_backup
    import json

    resp = json.loads(out.getvalue().strip())
    assert resp["error"]["code"] == -32700
    assert resp["id"] is None


def test_invalid_request_missing_method(providers, env, quota):
    from freellmpool.mcp_server import handle_message
    from freellmpool.router import Pool

    pool = Pool(providers, quota=quota, env=env)
    resp = handle_message(pool, {"jsonrpc": "2.0", "id": 5})  # has id, no method
    assert resp["error"]["code"] == -32600
    assert resp["id"] == 5
    # a non-dict is an invalid request with id null
    assert handle_message(pool, 42)["error"]["code"] == -32600


def test_batch_returns_single_json_array(providers, env, quota):
    import io
    import json
    import sys

    from freellmpool.mcp_server import serve_stdio
    from freellmpool.router import Pool

    batch = json.dumps(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},  # no reply
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
    )
    out = io.StringIO()
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(batch + "\n")
    sys.stdout = out
    try:
        serve_stdio(Pool(providers, quota=quota, env=env))
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1  # one line, one JSON-RPC array (not 2 separate objects)
    arr = json.loads(lines[0])
    assert isinstance(arr, list) and len(arr) == 2  # notification omitted
    assert {r["id"] for r in arr} == {1, 2}


# ---------------------------------------------------------------------------
# WU-011: new agent-facing UX tools (roles, recipe, battle, second_opinion,
# tailnet info, quota-wise). Keep free_llm_panel for backward compatibility.
# ---------------------------------------------------------------------------


def test_tools_call_roles_lists_bundled_roles(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {"jsonrpc": "2.0", "id": 100, "method": "tools/call", "params": {"name": "free_llm_roles"}},
    )
    text = resp["result"]["content"][0]["text"]
    assert resp["result"]["isError"] is False
    # Spot-check a few role names from roles.py.
    for role_name in ("coder", "critic", "summarizer", "second-opinion", "fast", "cheap"):
        assert role_name in text


def test_tools_call_roles_returns_single_role_details(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "tools/call",
            "params": {"name": "free_llm_roles", "arguments": {"name": "coder"}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert resp["result"]["isError"] is False
    assert "coder" in text
    assert "routing" in text


def test_tools_call_roles_unknown_role_is_tool_error(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 102,
            "method": "tools/call",
            "params": {"name": "free_llm_roles", "arguments": {"name": "no-such-role"}},
        },
    )
    assert resp["result"]["isError"] is True


def test_tools_call_second_opinion_clamps_count(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 110,
            "method": "tools/call",
            "params": {
                "name": "free_llm_second_opinion",
                "arguments": {"prompt": "hi", "n": 99},
            },
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert resp["result"]["isError"] is False
    # 6 fake providers, panel cap is 5 → 5 sections.
    assert text.count("###") == 5


def test_tools_call_second_opinion_reuses_panel_helper(providers, env, quota):
    """Second-opinion must reuse the panel behavior, not duplicate it."""
    import freellmpool.mcp_server as mcp

    # After import, the second_opinion handler is the same function object as panel.
    assert mcp._tool_second_opinion is mcp._tool_panel


def test_tools_call_second_opinion_missing_prompt_is_tool_error(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 111,
            "method": "tools/call",
            "params": {"name": "free_llm_second_opinion", "arguments": {}},
        },
    )
    assert resp["result"]["isError"] is True


def test_tools_call_battle_renders_comparison_markdown(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 120,
            "method": "tools/call",
            "params": {"name": "free_llm_battle", "arguments": {"prompt": "compare"}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert resp["result"]["isError"] is False
    assert "# freellmpool battle" in text
    assert "| model | result |" in text


def test_tools_call_battle_per_model_failures_stay_visible(providers, env, quota):
    """A failing provider must not abort the whole battle."""
    post = make_post({"alpha.test": (500, {"error": "down"})})
    pool = _pool(providers, env, quota, post=post)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 121,
            "method": "tools/call",
            "params": {"name": "free_llm_battle", "arguments": {"prompt": "hi", "n": 3}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    # Tool still returns content (not isError), and the per-model failure is visible.
    assert resp["result"]["isError"] is False
    assert "failed:" in text


def test_tools_call_battle_missing_prompt_is_tool_error(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 122,
            "method": "tools/call",
            "params": {"name": "free_llm_battle", "arguments": {"prompt": "  "}},
        },
    )
    assert resp["result"]["isError"] is True


def test_tools_call_recipe_runs_text_recipe_with_fake_providers(providers, env, quota):
    """free_llm_recipe runs a real bundled recipe end-to-end with fake providers."""
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 130,
            "method": "tools/call",
            "params": {
                "name": "free_llm_recipe",
                "arguments": {"name": "pr-review", "prompt": "diff --git a/app.py b/app.py\n"},
            },
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert resp["result"]["isError"] is False
    assert "pr-review" in text
    # Fake providers return "ok" via the default post script.
    assert "ok" in text


def test_tools_call_recipe_panel_recipe_renders_panel(providers, env, quota):
    """A panel-output recipe (second-opinion) reuses run_panel + render_panel_markdown."""
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 131,
            "method": "tools/call",
            "params": {
                "name": "free_llm_recipe",
                "arguments": {"name": "second-opinion", "prompt": "is this safe?", "opinions": 2},
            },
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert resp["result"]["isError"] is False
    assert "freellmpool panel" in text
    assert text.count("###") >= 2


def test_tools_call_recipe_unknown_name_is_tool_error(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 132,
            "method": "tools/call",
            "params": {"name": "free_llm_recipe", "arguments": {"name": "nope"}},
        },
    )
    assert resp["result"]["isError"] is True
    assert "unknown recipe" in resp["result"]["content"][0]["text"]


def test_tools_call_recipe_missing_name_is_tool_error(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 133,
            "method": "tools/call",
            "params": {"name": "free_llm_recipe", "arguments": {}},
        },
    )
    assert resp["result"]["isError"] is True


def test_tools_call_recipe_missing_variable_is_tool_error_not_traceback(providers, env, quota):
    """metaswarm-worker-review requires a non-empty `validation_output`."""
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 134,
            "method": "tools/call",
            "params": {
                "name": "free_llm_recipe",
                "arguments": {"name": "metaswarm-worker-review", "prompt": "worker summary"},
            },
        },
    )
    assert resp["result"]["isError"] is True
    text = resp["result"]["content"][0]["text"]
    assert "validation_output" in text
    assert "Traceback" not in text


def test_tools_call_recipe_missing_input_is_tool_error_not_traceback(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 135,
            "method": "tools/call",
            "params": {"name": "free_llm_recipe", "arguments": {"name": "pr-review"}},
        },
    )
    assert resp["result"]["isError"] is True
    text = resp["result"]["content"][0]["text"]
    assert "Traceback" not in text


def test_tools_call_tailnet_info_handles_missing_tailscale(providers, env, quota, monkeypatch):
    """When tailscale is not on PATH, the tool degrades gracefully (no crash, no leak)."""
    import freellmpool.tailnet as tailnet

    monkeypatch.setattr(tailnet.shutil, "which", lambda _: None)
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 140,
            "method": "tools/call",
            "params": {"name": "free_llm_tailnet_info", "arguments": {}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    # Degraded path returns a friendly status, no isError, no provider-key strings.
    assert resp["result"].get("isError") in (False, None)
    assert "Tailnet:" in text
    assert "tailscale" in text.lower() or "tailnet" in text.lower()
    # No provider-shaped env leaks into the response.
    forbidden_substrings = ("GROQ_API_KEY", "CEREBRAS_API_KEY", "OPENAI_API_KEY=", "ANTHROPIC_API_KEY=")
    for needle in forbidden_substrings:
        assert needle not in text


def test_tools_call_tailnet_info_usable_path_uses_placeholder_token(providers, env, quota, monkeypatch):
    """When tailscale reports a usable 100.x IPv4, the response uses a placeholder proxy key."""
    from types import SimpleNamespace

    import freellmpool.tailnet as tailnet

    monkeypatch.setattr(tailnet.shutil, "which", lambda _: "/usr/bin/tailscale")

    def fake_runner(args, timeout):
        return SimpleNamespace(returncode=0, stdout="100.64.0.42\n", stderr="", args=tuple(args))

    monkeypatch.setattr(tailnet, "_run_tailscale", lambda args, *, timeout, binary: fake_runner(args, timeout))

    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 141,
            "method": "tools/call",
            "params": {"name": "free_llm_tailnet_info", "arguments": {"port": 9090}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert "100.64.0.42" in text
    # Real proxy bearer tokens are NEVER exposed.
    # The hints block uses <proxy-key> as a placeholder.
    assert "<proxy-key>" in text
    # Provider API keys are never exposed.
    for needle in ("GROQ_API_KEY=", "CEREBRAS_API_KEY=", "OPENROUTER_API_KEY="):
        assert needle not in text


def test_tools_call_tailnet_info_rejects_invalid_port(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 142,
            "method": "tools/call",
            "params": {"name": "free_llm_tailnet_info", "arguments": {"port": 999999}},
        },
    )
    assert resp["result"]["isError"] is True


def test_tools_call_quota_wise_renders_local_headroom(providers, env, quota, monkeypatch):
    """quota_wise must render local counters and not recommend rotation / bypass."""
    pool = _pool(providers, env, quota)
    # Add a recorded call so the snapshot has data.
    pool.ask("warm")
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 150,
            "method": "tools/call",
            "params": {"name": "free_llm_quota_wise"},
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert resp["result"]["isError"] is False
    assert "Quota-wise" in text or "quota" in text.lower()
    assert "advice" in text.lower()
    # Anti-bypass / anti-rotation guarantees.
    forbidden = ("rotate account", "rotate accounts", "bypass rate", "rate-limit bypass", "automatic paid", "paid fallback", "switch account", "switch accounts")
    for needle in forbidden:
        assert needle not in text.lower()
    # Only acceptable phrasing is allowed (substring presence proves the advice is shaped).
    assert "local counter rollover at utc midnight" in text.lower()
    assert "upstream providers use their own limit/reset windows" in text.lower()
    assert "lower fan-out" in text.lower()


def test_tools_call_quota_wise_never_leaks_keys_or_tokens(providers, env, quota):
    pool = _pool(providers, env, quota)
    resp = handle_message(
        pool,
        {
            "jsonrpc": "2.0",
            "id": 151,
            "method": "tools/call",
            "params": {"name": "free_llm_quota_wise"},
        },
    )
    text = resp["result"]["content"][0]["text"]
    for needle in ("GROQ_API_KEY", "CEREBRAS_API_KEY", "bearer", "Authorization:"):
        assert needle not in text
