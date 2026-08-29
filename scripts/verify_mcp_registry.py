#!/usr/bin/env python3
"""Verify that the official MCP Registry exactly reproduces ``server.json``."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REGISTRY_ENDPOINT = "https://registry.modelcontextprotocol.io/v0.1/servers"
OFFICIAL_META_KEY = "io.modelcontextprotocol.registry/official"
MAX_RESPONSE_BYTES = 1_000_000


class RegistryVerificationError(ValueError):
    """The published registry entry is absent, stale, or non-reproducible."""


def verify_payload(manifest: dict[str, Any], payload: object) -> None:
    """Require one active/latest registry entry identical to ``manifest``."""
    if not isinstance(payload, dict) or not isinstance(payload.get("servers"), list):
        raise RegistryVerificationError("registry response has no servers list")

    name = manifest.get("name")
    version = manifest.get("version")
    matches = []
    for entry in payload["servers"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("server"), dict):
            continue
        server = entry["server"]
        if server.get("name") == name and server.get("version") == version:
            matches.append(entry)
    if len(matches) != 1:
        raise RegistryVerificationError(
            "registry must contain exactly one entry for "
            f"{name}@{version}; found {len(matches)}"
        )

    entry = matches[0]
    if entry["server"] != manifest:
        raise RegistryVerificationError(
            "published server manifest does not exactly match the tagged server.json"
        )

    metadata = entry.get("_meta")
    official = metadata.get(OFFICIAL_META_KEY) if isinstance(metadata, dict) else None
    if not isinstance(official, dict) or official.get("status") != "active":
        raise RegistryVerificationError("published server is not active")
    if official.get("isLatest") is not True:
        raise RegistryVerificationError("published server is not marked latest")


def fetch_payload(name: str, *, timeout: float = 20.0) -> object:
    """Fetch the official registry's latest search result with a size bound."""
    query = urllib.parse.urlencode({"search": name, "version": "latest"})
    request = urllib.request.Request(
        f"{REGISTRY_ENDPOINT}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "freellmpool-release-verifier/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise RegistryVerificationError(f"registry request failed: {exc}") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise RegistryVerificationError("registry response exceeds size limit")
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryVerificationError("registry returned invalid JSON") from exc


def verify_registry(path: Path, *, timeout: float = 20.0) -> None:
    """Load a local manifest and compare it with the official latest entry."""
    try:
        manifest = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryVerificationError(f"cannot load manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise RegistryVerificationError("manifest must be a JSON object")
    name = manifest.get("name")
    if not isinstance(name, str) or not name:
        raise RegistryVerificationError("manifest has no server name")
    verify_payload(manifest, fetch_payload(name, timeout=timeout))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", type=Path, default=Path("server.json"))
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)
    try:
        verify_registry(args.manifest, timeout=args.timeout)
    except RegistryVerificationError as exc:
        print(f"MCP registry verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"MCP registry matches {args.manifest} exactly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
