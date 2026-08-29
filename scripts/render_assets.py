#!/usr/bin/env python3
"""Render tracked SVG cards to PNG and refresh their integrity manifest."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

if __package__:
    from .asset_contract import (
        ASSETS,
        FONT_MATCHES,
        FONT_SOURCES,
        FONTCONFIG_VERSION,
        RENDERER_VERSION,
    )
else:
    from asset_contract import (
        ASSETS,
        FONT_MATCHES,
        FONT_SOURCES,
        FONTCONFIG_VERSION,
        RENDERER_VERSION,
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _isolated_font_environment(workspace: Path) -> dict[str, str]:
    font_match = shutil.which("fc-match")
    if font_match is None:
        raise SystemExit("fc-match is required to verify deterministic raster fonts")
    fontconfig_version = subprocess.run(
        [font_match, "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stderr.strip()
    if fontconfig_version != FONTCONFIG_VERSION:
        raise SystemExit(
            "fontconfig version mismatch: "
            f"expected {FONTCONFIG_VERSION!r}, got {fontconfig_version!r}"
        )

    font_dir = workspace / "fonts"
    cache_dir = workspace / "font-cache"
    font_dir.mkdir()
    cache_dir.mkdir()
    for filename, (source_name, expected_hash) in FONT_SOURCES.items():
        source = Path(source_name)
        if not source.is_file() or source.is_symlink():
            raise SystemExit(f"required deterministic font is unavailable: {source}")
        actual_hash = _digest(source)
        if actual_hash != expected_hash:
            raise SystemExit(
                f"font hash mismatch for {source}: expected {expected_hash}, got {actual_hash}"
            )
        shutil.copyfile(source, font_dir / filename)

    config = workspace / "fonts.conf"
    config.write_text(
        "<?xml version=\"1.0\"?>\n"
        "<!DOCTYPE fontconfig SYSTEM \"urn:fontconfig:fonts.dtd\">\n"
        "<fontconfig>\n"
        f"  <dir>{escape(font_dir.as_posix())}</dir>\n"
        f"  <cachedir>{escape(cache_dir.as_posix())}</cachedir>\n"
        "</fontconfig>\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "FONTCONFIG_FILE": str(config),
            "FONTCONFIG_PATH": str(workspace),
            "XDG_CACHE_HOME": str(cache_dir),
        }
    )
    for pattern, expected_filename in FONT_MATCHES.items():
        matched = subprocess.run(
            [font_match, "--format", "%{file}", pattern],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        ).stdout
        if Path(matched) != font_dir / expected_filename:
            raise SystemExit(
                f"isolated font match failed for {pattern!r}: got {matched!r}"
            )
    return environment


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    assets = root / "assets"
    docs_assets = root / "docs" / "assets"
    renderer = shutil.which("rsvg-convert")
    if renderer is None:
        raise SystemExit("rsvg-convert is required to regenerate raster assets")
    version = subprocess.run(
        [renderer, "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if version != RENDERER_VERSION:
        raise SystemExit(
            f"renderer version mismatch: expected {RENDERER_VERSION!r}, got {version!r}"
        )

    records: dict[str, dict[str, str]] = {}
    with tempfile.TemporaryDirectory(prefix="freellmpool-asset-fonts-") as temporary_root:
        environment = _isolated_font_environment(Path(temporary_root))
        for name in ASSETS:
            source = assets / f"{name}.svg"
            target = assets / f"{name}.png"
            with tempfile.NamedTemporaryFile(
                prefix=f".{name}-",
                suffix=".png",
                dir=assets,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
            try:
                subprocess.run(
                    [renderer, "--output", str(temporary), str(source)],
                    check=True,
                    env=environment,
                )
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            shutil.copyfile(target, docs_assets / target.name)
            records[name] = {
                "svg_sha256": _digest(source),
                "png_sha256": _digest(target),
            }

    manifest = {
        "schema_version": 2,
        "renderer": version,
        "fontconfig": FONTCONFIG_VERSION,
        "fonts": {
            filename: expected_hash
            for filename, (_source, expected_hash) in FONT_SOURCES.items()
        },
        "assets": records,
    }
    (assets / "asset-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(f"Rendered {len(ASSETS)} SVG cards with {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
