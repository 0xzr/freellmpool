from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "opencode": {
        "name": "opencode-freellmpool",
        "entry": "freellmpool.js",
        "files": {"freellmpool.js", "README.md", "LICENSE"},
    },
    "opencode-tui": {
        "name": "opencode-freellmpool-tui",
        "entry": "index.tsx",
        "files": {"index.tsx", "README.md", "LICENSE"},
    },
}


def _manifest(directory: str) -> dict[str, object]:
    return json.loads(
        (ROOT / "integrations" / directory / "package.json").read_text(encoding="utf-8")
    )


def test_opencode_package_manifests_are_publishable_and_explicit() -> None:
    names: set[str] = set()
    for directory, expected in PACKAGES.items():
        manifest = _manifest(directory)
        names.add(str(manifest["name"]))
        assert manifest["name"] == expected["name"]
        assert manifest["version"] == "0.1.0"
        assert manifest["private"] is False
        assert manifest["type"] == "module"
        assert manifest["license"] == "MIT"
        assert manifest["files"] == sorted(expected["files"])
        assert manifest["engines"] == {"node": ">=20"}
        assert manifest["repository"] == {
            "type": "git",
            "url": "git+https://github.com/0xzr/freellmpool.git",
            "directory": f"integrations/{directory}",
        }
        assert manifest["homepage"] == (
            f"https://github.com/0xzr/freellmpool/tree/main/integrations/{directory}#readme"
        )
        assert manifest["bugs"] == {
            "url": "https://github.com/0xzr/freellmpool/issues"
        }
        assert "scripts" not in manifest
        assert manifest["exports"]["."] == f"./{expected['entry']}"
        assert (ROOT / "integrations" / directory / "LICENSE").is_file()

    assert names == {"opencode-freellmpool", "opencode-freellmpool-tui"}
    assert _manifest("opencode")["peerDependencies"] == {
        "@opencode-ai/plugin": ">=1.14.0"
    }
    assert _manifest("opencode-tui")["exports"]["./tui"] == "./index.tsx"
    assert _manifest("opencode-tui")["peerDependencies"] == {
        "@opentui/solid": ">=0.4.5",
        "solid-js": ">=1.9.12",
    }


def test_opencode_packages_use_the_proxy_default_and_current_command() -> None:
    paths = [
        ROOT / "integrations" / "opencode" / "freellmpool.js",
        ROOT / "integrations" / "opencode" / "README.md",
        ROOT / "integrations" / "opencode-tui" / "index.tsx",
        ROOT / "integrations" / "opencode-tui" / "README.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "http://localhost:8080" in combined
    assert "localhost:8765" not in combined
    assert "freellmpool-proxy" not in combined
    assert "freellmpool proxy" in combined


def test_package_smoke_script_checks_tarballs_clean_installs_and_loads() -> None:
    script = (ROOT / "scripts" / "check_opencode_packages.mjs").read_text(
        encoding="utf-8"
    )
    for required in (
        "npm pack --json",
        "--ignore-scripts",
        "--omit=peer",
        "opencode-freellmpool",
        "opencode-freellmpool-tui/tui",
        "bun",
        "LICENSE",
    ):
        assert required in script


def test_unpublished_registry_install_is_labelled_pending_everywhere() -> None:
    paths = [
        ROOT / "docs" / "INTEGRATIONS.md",
        ROOT / "docs" / "run-opencode-on-free-models.html",
        ROOT / "integrations" / "opencode" / "README.md",
        ROOT / "integrations" / "opencode-tui" / "README.md",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "Registry publication status: pending" in text
        assert "opencode-freellmpool" in text
        assert "opencode-freellmpool-tui" in text
