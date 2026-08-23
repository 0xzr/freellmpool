from __future__ import annotations

import re
import tomllib
import xml.dom.minidom
from pathlib import Path

from freellmpool import __version__
from freellmpool.config import load_catalog

ROOT = Path(__file__).resolve().parents[1]


def test_faq_lists_every_builtin_chat_provider():
    with (ROOT / "src/freellmpool/providers.toml").open("rb") as fh:
        providers = tomllib.load(fh)["provider"]

    faq = (ROOT / "FAQ.md").read_text(encoding="utf-8")

    assert len(providers) == 24
    for provider in providers:
        assert f"`{provider['id']}`" in faq


def test_readme_links_faq_prominently():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    first_screen = readme.split("## Run a coding agent on free models", 1)[0]

    assert "[FAQ](FAQ.md)" in first_screen


def test_readme_comparison_table_has_required_p4_shape():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## How it compares", 1)[1].split("## FAQ", 1)[0]

    for column in (
        "Keyless start",
        "# providers",
        "Failover",
        "MCP server",
        "CLI",
        "Transcription",
        "Local/self-hosted",
        "License",
    ):
        assert column in section

    for row in ("**freellmpool**", "OpenRouter free models", "LiteLLM", "FreeLLMAPI"):
        assert row in section

    assert "FreeLLMAPI predates this project" in section
    assert "independent convergence" in section


def test_readme_opens_with_tokenmax_demo_assets():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    opening = readme.split("## 30-second quickstart", 1)[0]

    assert "assets/demo.svg" in opening
    assert "assets/tokenmax-results.svg" in opening
    assert "tokenmax" in opening.lower()


def test_demo_assets_are_well_formed_and_current():
    demo = (ROOT / "assets/demo.svg").read_text(encoding="utf-8")
    results = (ROOT / "assets/tokenmax-results.svg").read_text(encoding="utf-8")

    xml.dom.minidom.parseString(demo)
    xml.dom.minidom.parseString(results)
    assert "TOKENMAXXING" in demo
    assert "--animation-duration: 8500ms" in demo
    assert f"freellmpool-{__version__}" in demo
    assert "24 cataloged providers, 223 enabled routes" in demo
    assert "keyless start when available" in demo
    assert "223" in results
    assert "407 cataloged" in results
    assert "cataloged providers" in results
    assert "$0" in results

    provider_ids = {provider.id for provider in load_catalog()}
    displayed_routes = {
        value
        for value in re.findall(
            r'<tspan class="(?:cyan|purple|yellow|green)">([^<]+/[^<]+)</tspan>', demo
        )
        if value.split("/", 1)[0] in provider_ids
    }
    automatic_routes = {
        f"{provider.id}/{model.name}"
        for provider in load_catalog()
        for model in provider.models
        if model.enabled and model.auto
    }
    assert displayed_routes
    assert displayed_routes <= automatic_routes

    assert (ROOT / "docs/assets/demo.png").read_bytes() == (
        ROOT / "assets/demo.png"
    ).read_bytes()
