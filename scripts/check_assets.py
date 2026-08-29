#!/usr/bin/env python3
"""Verify generated social/demo rasters against their SVG source manifest."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

if __package__:
    from .asset_contract import ASSETS, FONT_SOURCES, FONTCONFIG_VERSION, RENDERER_VERSION
else:
    from asset_contract import ASSETS, FONT_SOURCES, FONTCONFIG_VERSION, RENDERER_VERSION

MANIFEST_PATH = Path("assets/asset-manifest.json")
MAX_MANIFEST_BYTES = 64_000
ASSET_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
SHA256 = re.compile(r"[0-9a-f]{64}")


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def check_assets(root: Path) -> list[str]:
    """Return errors for stale sources, rasters, or GitHub Pages copies."""
    manifest_path = root / MANIFEST_PATH
    try:
        raw = manifest_path.read_bytes()
        if len(raw) > MAX_MANIFEST_BYTES:
            return ["asset manifest exceeds size limit"]
        manifest = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"cannot load asset manifest: {exc}"]
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "renderer",
        "fontconfig",
        "fonts",
        "assets",
    }:
        return ["asset manifest has unknown or missing fields"]
    if manifest["schema_version"] != 2:
        return ["asset manifest schema is invalid"]
    if manifest["renderer"] != RENDERER_VERSION:
        return [f"asset renderer must be exactly {RENDERER_VERSION}"]
    if manifest["fontconfig"] != FONTCONFIG_VERSION:
        return [f"asset fontconfig must be exactly {FONTCONFIG_VERSION}"]
    expected_fonts = {
        filename: expected_hash
        for filename, (_source, expected_hash) in FONT_SOURCES.items()
    }
    if manifest["fonts"] != expected_fonts:
        return ["asset font hashes do not match the deterministic render contract"]
    assets = manifest["assets"]
    if not isinstance(assets, dict):
        return ["asset manifest must contain assets"]
    expected_names = set(ASSETS)
    actual_names = set(assets)
    if actual_names != expected_names:
        missing = ", ".join(sorted(expected_names - actual_names)) or "none"
        extra = ", ".join(sorted(actual_names - expected_names)) or "none"
        return [
            "asset records must exactly match generated assets "
            f"(missing: {missing}; extra: {extra})"
        ]

    errors: list[str] = []
    for name, expected in sorted(assets.items()):
        if not isinstance(name, str) or ASSET_NAME.fullmatch(name) is None:
            errors.append(f"invalid asset name: {name!r}")
            continue
        if not isinstance(expected, dict) or set(expected) != {
            "svg_sha256",
            "png_sha256",
        }:
            errors.append(f"{name}: invalid manifest record")
            continue
        svg_hash = expected["svg_sha256"]
        png_hash = expected["png_sha256"]
        if not isinstance(svg_hash, str) or SHA256.fullmatch(svg_hash) is None:
            errors.append(f"{name}: invalid SVG hash")
            continue
        if not isinstance(png_hash, str) or SHA256.fullmatch(png_hash) is None:
            errors.append(f"{name}: invalid PNG hash")
            continue

        source = root / "assets" / f"{name}.svg"
        raster = root / "assets" / f"{name}.png"
        pages_copy = root / "docs" / "assets" / f"{name}.png"
        try:
            current_svg_hash = _digest(source)
            current_png_hash = _digest(raster)
            pages_hash = _digest(pages_copy)
        except OSError as exc:
            errors.append(f"{name}: cannot read required asset: {exc}")
            continue
        if current_svg_hash != svg_hash:
            errors.append(f"{name}.svg hash drift; regenerate raster assets")
        if current_png_hash != png_hash:
            errors.append(f"{name}.png hash drift; refresh asset manifest")
        if pages_hash != current_png_hash:
            errors.append(f"docs/assets/{name}.png differs from assets/{name}.png")
    return errors


def main() -> int:
    errors = check_assets(Path(__file__).resolve().parent.parent)
    if errors:
        print("Generated asset drift detected:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("Generated SVG/PNG assets match their recorded source hashes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
