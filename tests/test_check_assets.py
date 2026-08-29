from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.asset_contract import ASSETS, FONT_SOURCES, FONTCONFIG_VERSION, RENDERER_VERSION
from scripts.check_assets import check_assets

ROOT = Path(__file__).resolve().parents[1]


def test_committed_raster_asset_manifest_is_current():
    assert check_assets(ROOT) == []


def test_asset_gate_rejects_changed_source_and_divergent_pages_copy(tmp_path):
    assets = tmp_path / "assets"
    docs_assets = tmp_path / "docs" / "assets"
    assets.mkdir(parents=True)
    docs_assets.mkdir(parents=True)
    records = {}
    for name in ASSETS:
        svg = f"<svg>{name}</svg>".encode()
        png = f"{name}-png".encode()
        (assets / f"{name}.svg").write_bytes(svg)
        (assets / f"{name}.png").write_bytes(png)
        (docs_assets / f"{name}.png").write_bytes(png)
        records[name] = {
            "svg_sha256": hashlib.sha256(svg).hexdigest(),
            "png_sha256": hashlib.sha256(png).hexdigest(),
        }
    records["demo"] = {"svg_sha256": "0" * 64, "png_sha256": "1" * 64}
    (docs_assets / "demo.png").write_bytes(b"different-png")
    (assets / "asset-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "renderer": RENDERER_VERSION,
                "fontconfig": FONTCONFIG_VERSION,
                "fonts": {
                    filename: digest
                    for filename, (_source, digest) in FONT_SOURCES.items()
                },
                "assets": records,
            }
        ),
        encoding="utf-8",
    )

    errors = check_assets(tmp_path)

    assert any("demo.svg hash drift" in error for error in errors)
    assert any("demo.png hash drift" in error for error in errors)
    assert any("docs/assets/demo.png differs" in error for error in errors)


def test_asset_gate_requires_every_generated_asset_and_rejects_unknown_records(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    record = {"svg_sha256": "0" * 64, "png_sha256": "1" * 64}

    for names in (
        {"demo": record},
        {
            "demo": record,
            "social-preview": record,
            "tokenmax-results": record,
            "untracked-preview": record,
        },
    ):
        (assets / "asset-manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "renderer": RENDERER_VERSION,
                    "fontconfig": FONTCONFIG_VERSION,
                    "fonts": {
                        filename: digest
                        for filename, (_source, digest) in FONT_SOURCES.items()
                    },
                    "assets": names,
                }
            ),
            encoding="utf-8",
        )

        errors = check_assets(tmp_path)

        assert any("asset records must exactly match" in error for error in errors)


def test_asset_gate_rejects_renderer_version_drift(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "asset-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "renderer": "rsvg-convert version 999.0",
                "fontconfig": FONTCONFIG_VERSION,
                "fonts": {
                    filename: digest
                    for filename, (_source, digest) in FONT_SOURCES.items()
                },
                "assets": {},
            }
        ),
        encoding="utf-8",
    )

    assert check_assets(tmp_path) == [
        f"asset renderer must be exactly {RENDERER_VERSION}"
    ]


def test_asset_manifest_records_exact_font_environment():
    manifest = json.loads(
        (ROOT / "assets" / "asset-manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["schema_version"] == 2
    assert manifest["fontconfig"] == FONTCONFIG_VERSION
    assert manifest["fonts"] == {
        filename: digest for filename, (_source, digest) in FONT_SOURCES.items()
    }
    for svg in (ROOT / "assets").glob("*.svg"):
        content = svg.read_text(encoding="utf-8")
        assert "Inter" not in content
        assert "Segoe UI" not in content
        assert "system-ui" not in content
