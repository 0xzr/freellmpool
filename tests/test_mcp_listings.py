from __future__ import annotations

import json
from pathlib import Path

from freellmpool import __version__

ROOT = Path(__file__).resolve().parents[1]

REGISTRIES = ("Official MCP Registry", "Smithery", "Glama", "MCP.so", "PulseMCP")
LISTING_FILES = (
    "smithery.md",
    "glama-submission.md",
    "mcp-so-issue.md",
    "pulsemcp-submission.md",
)
# Tools actually exposed by the MCP server. Listings must mention exactly these.
MCP_TOOLS = (
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
)


def test_server_json_is_registry_ready_for_stdio_package():
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    package = server["packages"][0]

    assert server["$schema"] == "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
    assert server["name"] == "io.github.0xzr/freellmpool"
    assert server["version"] == __version__
    assert server["repository"] == {
        "url": "https://github.com/0xzr/freellmpool",
        "source": "github",
    }
    assert len(server["description"]) <= 100
    assert "24 LLM providers" in server["description"]
    assert "OpenAI-compatible" in server["description"]
    assert "MCP" in server["description"]
    assert package["registryType"] == "pypi"
    assert package["identifier"] == "freellmpool"
    assert package["version"] == __version__
    assert package["runtimeHint"] == "uvx"
    assert package["transport"] == {"type": "stdio"}
    assert package["packageArguments"] == [{"type": "positional", "value": "mcp"}]


def test_mcp_listing_status_covers_each_registry_and_action():
    doc = (ROOT / "docs/MCP_LISTINGS.md").read_text(encoding="utf-8")

    assert "official MCP Registry publish" in doc
    assert "MCP.so issue" in doc
    assert "remaining directories require the operator" in doc
    for registry in REGISTRIES:
        assert registry in doc
    for filename in LISTING_FILES:
        assert f"docs/mcp-listings/{filename}" in doc
    for required in (
        "https://smithery.ai/docs/build/publish",
        "https://glama.ai/mcp/methodology",
        "https://mcp.so/",
        "https://www.pulsemcp.com/api",
        "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
    ):
        assert required in doc


def test_mcp_listing_submission_files_have_required_copy():
    for filename in LISTING_FILES:
        text = (ROOT / "docs/mcp-listings" / filename).read_text(encoding="utf-8")
        assert "freellmpool" in text
        assert "https://github.com/0xzr/freellmpool" in text
        assert '"command": "uvx"' in text or "MCPB" in text
        assert "stdio" in text.lower()
        for tool in MCP_TOOLS:
            if filename != "smithery.md":
                assert tool in text
