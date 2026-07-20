from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from freellmpool import __version__, client

ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_versions_match_package() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    version = pyproject["project"]["version"]
    server = json.loads((ROOT / "server.json").read_text())
    docs = (ROOT / "docs" / "index.html").read_text()
    demo = (ROOT / "assets" / "demo.svg").read_text()
    readme = (ROOT / "README.md").read_text()

    assert __version__ == version
    assert server["version"] == version
    assert server["packages"][0]["version"] == version
    project_description = pyproject["project"]["description"]
    provider_count = re.search(r"(\d+) LLM providers", project_description)
    model_count = re.search(r"(\d+) cataloged chat models", project_description)
    enabled_route_count = re.search(r"\((\d+) enabled chat routes,", readme)
    assert provider_count is not None
    assert model_count is not None
    assert enabled_route_count is not None
    assert f"{provider_count.group(1)} LLM providers" in server["description"]
    assert f"{provider_count.group(1)} LLM providers" in readme
    assert f"{model_count.group(1)} cataloged" in readme
    assert f"{enabled_route_count.group(1)} enabled chat routes" in docs
    assert f"{model_count.group(1)} cataloged" in docs
    assert f"Latest release: {version}" in docs
    assert f'"softwareVersion": "{version}"' in docs
    assert f"freellmpool-{version}" in demo
    assert f"{provider_count.group(1)} cataloged providers" in demo


def test_client_user_agent_uses_package_version() -> None:
    assert f"freellmpool/{__version__}" in client._USER_AGENT


def test_runtime_dependencies_guard_stdlib_first_contract() -> None:
    """The stdlib-first contract only allows httpx as a required runtime dependency."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["dependencies"] == ["httpx>=0.27"]


def test_readme_has_copy_pastable_tailnet_and_metaswarm_paths() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "freellmpool tailnet serve --port 8080" in readme
    assert "freellmpool tailnet connect <tailnet-ip> --port 8080" in readme
    assert "freellmpool init --yes --agent metaswarm --tailnet" in readme
    assert "freellmpool profile doctor metaswarm --dry-run" in readme


def test_readme_has_current_adoption_paths_and_pinned_comparison_sources() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    integrations = (ROOT / "docs" / "INTEGRATIONS.md").read_text(encoding="utf-8")

    assert 'uvx freellmpool ask --max-tokens 32' in readme
    assert "Comparison snapshot: 2026-07-19" in readme
    assert (
        "tashfeenahmed/freellmapi/blob/"
        "759de8e7ed1edc1cd513c9777cd0a807fb5ceee3/README.md"
    ) in readme
    assert (
        "diegosouzapw/OmniRoute/blob/"
        "d8ff51874c8add566d43225988b9bc67e0542d65/README.md"
    ) in readme
    assert "28 free LLM providers" in readme
    assert "MCP (Streamable HTTP)" in readme
    assert "OmniRoute" in readme

    for text in (readme, integrations):
        assert "freellmpool profile install hermes" in text
        assert "provider: custom" in text
        assert "default: quality" in text
        assert "http://localhost:8080/v1" in text
        assert "/livez" in text
        assert "/readyz" in text
        assert "/v1/providers" in text
        assert "/v1/models?ready=true" in text


def test_roadmap_reflects_kimi_m3_addendum() -> None:
    roadmap = (ROOT / "docs/ROADMAP.md").read_text(encoding="utf-8")
    assert "Top 10 feature map" in roadmap
    assert "Kimi/M3 Top-10 Planning Addendum" in roadmap
    assert "PYTHONPATH=src" in roadmap
    assert "No rate-limit bypass" in roadmap


def test_pypi_metadata_has_launch_surfaces() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = pyproject["project"]
    discovery = (ROOT / "docs/GITHUB_DISCOVERY.md").read_text(encoding="utf-8")

    assert len(project["description"]) <= 120
    assert f"> {project['description']}" in discovery

    urls = project["urls"]
    for name in ("Docs", "Changelog", "Issues", "Repository"):
        assert name in urls

    for keyword in (
        "anthropic",
        "claude",
        "cursor",
        "mcp",
        "model-context-protocol",
        "rate-limiting",
        "speech-to-text",
    ):
        assert keyword in project["keywords"]

    for classifier in (
        "Framework :: AsyncIO",
        "Operating System :: OS Independent",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ):
        assert classifier in project["classifiers"]

    dev_deps = project["optional-dependencies"]["dev"]
    assert any(dep.startswith("build>=") for dep in dev_deps)
    assert any(dep.startswith("twine>=") for dep in dev_deps)
