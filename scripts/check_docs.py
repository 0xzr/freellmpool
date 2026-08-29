#!/usr/bin/env python3
"""Validate bounded internal links and sitemap targets for GitHub Pages."""

from __future__ import annotations

import argparse
import posixpath
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

PAGES_BASE = "https://0xzr.github.io/freellmpool/"
PAGES_PATH = "/freellmpool/"
PAGES_HOST = "0xzr.github.io"
LINK_ATTRIBUTES = frozenset({"href", "src"})
IGNORED_SCHEMES = frozenset({"data", "javascript", "mailto", "tel"})


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name.casefold() in LINK_ATTRIBUTES and value:
                self.links.append(value.strip())


def _resolve_internal(root: Path, source: Path, raw_link: str) -> tuple[Path | None, str | None]:
    parsed = urllib.parse.urlsplit(raw_link)
    scheme = parsed.scheme.casefold()
    netloc = parsed.netloc.casefold()
    is_web_url = scheme in {"http", "https"} or (not scheme and bool(netloc))
    is_pages_url = is_web_url and netloc == PAGES_HOST
    if is_web_url and not is_pages_url:
        return None, None
    if not is_pages_url and (scheme in IGNORED_SCHEMES or parsed.netloc):
        return None, None
    if not parsed.path:
        return None, None

    path = urllib.parse.unquote(parsed.path)
    if is_pages_url and path == PAGES_PATH.rstrip("/"):
        relative = ""
    elif is_pages_url and path.startswith(PAGES_PATH):
        relative = path.removeprefix(PAGES_PATH)
        if relative.startswith("/"):
            return None, f"same-origin target is absolute after Pages base: {path}"
    elif is_pages_url:
        return None, f"same-origin path is outside the Pages base: {path}"
    elif path.startswith(PAGES_PATH):
        relative = path.removeprefix(PAGES_PATH)
        if relative.startswith("/"):
            return None, f"internal target is absolute after Pages base: {path}"
    elif path.startswith("/"):
        return None, f"absolute path is outside the Pages base: {path}"
    else:
        parent = source.relative_to(root).parent.as_posix()
        relative = posixpath.join(parent, path)
    normalized = posixpath.normpath(relative)
    if normalized == ".":
        normalized = "index.html"
    elif path.endswith("/"):
        normalized = posixpath.join(normalized, "index.html")
    if normalized == ".." or normalized.startswith("../"):
        return None, f"internal target escapes docs root: {path}"
    target = root.joinpath(*PurePosixPath(normalized).parts)
    try:
        target.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None, f"internal target escapes resolved docs root: {path}"
    return target, None


def _check_html_links(root: Path) -> list[str]:
    errors: list[str] = []
    for source in sorted(root.rglob("*.html")):
        parser = _LinkParser()
        try:
            parser.feed(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            errors.append(f"{source.relative_to(root)}: cannot parse HTML: {exc}")
            continue
        for link in parser.links:
            target, error = _resolve_internal(root, source, link)
            if error:
                errors.append(f"{source.relative_to(root)}: {error}")
            elif target is not None and not target.is_file():
                errors.append(
                    f"{source.relative_to(root)}: missing internal target "
                    f"{target.relative_to(root)}"
                )
    return errors


def _check_sitemap(root: Path) -> list[str]:
    sitemap = root / "sitemap.xml"
    try:
        document = ET.parse(sitemap)
    except (OSError, ET.ParseError) as exc:
        return [f"sitemap.xml: cannot parse: {exc}"]

    errors: list[str] = []
    locations = [
        (element.text or "").strip()
        for element in document.getroot().iter()
        if element.tag.rsplit("}", 1)[-1] == "loc"
    ]
    seen: set[str] = set()
    normalized_targets: set[str] = set()
    for location in locations:
        if location in seen:
            errors.append(f"sitemap.xml: duplicate sitemap location: {location}")
        seen.add(location)
        if not location.startswith(PAGES_BASE):
            errors.append(f"sitemap.xml: location must start with {PAGES_BASE}: {location}")
            continue
        relative = urllib.parse.urlsplit(location.removeprefix(PAGES_BASE)).path
        relative = urllib.parse.unquote(relative)
        if not relative or relative.endswith("/"):
            relative = posixpath.join(relative, "index.html")
        normalized = posixpath.normpath(relative)
        if normalized == ".." or normalized.startswith("../"):
            errors.append(f"sitemap.xml: target escapes docs root: {relative}")
            continue
        normalized_targets.add(normalized)
        target = root.joinpath(*PurePosixPath(normalized).parts)
        if not target.is_file():
            errors.append(f"sitemap.xml: sitemap target does not exist: {normalized}")
    if not locations:
        errors.append("sitemap.xml: no locations found")

    deployable_targets = {
        path.name
        for path in root.glob("*.html")
        if path.name != "404.html" and path.is_file()
    }
    deployable_targets.add("index.html")
    for target in sorted(deployable_targets - normalized_targets):
        errors.append(f"sitemap.xml: deployable page is not listed: {target}")
    for target in sorted(normalized_targets - deployable_targets):
        errors.append(f"sitemap.xml: non-deployable target is listed: {target}")
    return errors


def check_docs(root: Path) -> list[str]:
    """Return deterministic GitHub Pages integrity errors below ``root``."""
    if not root.is_dir():
        return [f"docs root is not a directory: {root}"]
    errors = [*_check_html_links(root), *_check_sitemap(root)]
    if not (root / "404.html").is_file():
        errors.append("custom 404.html is missing")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path("docs"))
    args = parser.parse_args(argv)
    errors = check_docs(args.root)
    if errors:
        print("Documentation integrity errors:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("Documentation links and sitemap targets are internally consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
