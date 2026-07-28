#!/usr/bin/env python3
"""Build a reproducible manifest for the frontier-model GitHub source screen.

The script intentionally uses the authenticated ``gh`` CLI instead of embedding
credentials. Search results are deduplicated by immutable Git blob SHA, each blob
is fetched and decoded, and only successfully inspected blobs enter the manifest.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MODEL_TERMS = ("GLM-5.2", "Kimi-K2.7", "MiniMax-M3", "Qwen3.6")
RUNTIME_TERMS = (
    "headerTimeout",
    "chunkTimeout",
    "opencode provider registry",
    "opencode openai-compatible",
    "models.dev provider",
)
QUERIES = tuple((term, 30, "model") for term in MODEL_TERMS) + tuple(
    (term, 20, "runtime") for term in RUNTIME_TERMS
)
MATCH_TERMS = MODEL_TERMS + (
    "headerTimeout",
    "chunkTimeout",
    "opencode",
    "provider",
    "models.dev",
)
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _gh_json(*args: str) -> dict:
    completed = subprocess.run(
        ("gh", "api", *args),
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise ValueError("GitHub API response was not an object")
    return value


def _search(query: str, per_page: int) -> list[dict]:
    result = _gh_json(
        "--method",
        "GET",
        "search/code",
        "-f",
        f"q={query} in:file",
        "-f",
        f"per_page={per_page}",
    )
    return [item for item in result.get("items", []) if isinstance(item, dict)]


def _inspect(item: dict, matched_queries: list[str]) -> dict:
    blob = _gh_json(str(item["git_url"]))
    raw = base64.b64decode(blob.get("content", ""), validate=False)
    text = raw.decode("utf-8", errors="replace")
    lowered = text.lower()
    repository = item["repository"]["full_name"]
    blob_sha = str(blob["sha"])
    refs = parse_qs(urlparse(str(item["url"])).query).get("ref", [])
    commit_sha = refs[0] if len(refs) == 1 else ""
    immutable_url = str(item["html_url"])
    if not _FULL_SHA_RE.fullmatch(blob_sha):
        raise ValueError("Git blob response did not contain a full SHA")
    if not _FULL_SHA_RE.fullmatch(commit_sha):
        raise ValueError("code-search result did not contain a full commit ref")
    if f"/blob/{commit_sha}/" not in immutable_url:
        raise ValueError("code-search html_url is not pinned to its commit ref")
    return {
        "repository": repository,
        "path": item["path"],
        "commit_sha": commit_sha,
        "blob_sha": blob_sha,
        "bytes": len(raw),
        "matched_queries": sorted(matched_queries),
        "matched_terms": sorted(term for term in MATCH_TERMS if term.lower() in lowered),
        "immutable_url": immutable_url,
        "blob_api_url": str(item["git_url"]),
        "contents_api_url": str(item["url"]),
    }


def build_manifest(workers: int = 8) -> dict:
    by_sha: dict[str, dict] = {}
    query_rows: list[dict] = []
    for query, requested, category in QUERIES:
        items = _search(query, requested)
        query_rows.append(
            {
                "query": f"{query} in:file",
                "category": category,
                "requested": requested,
                "returned": len(items),
            }
        )
        for item in items:
            sha = str(item.get("sha", ""))
            repository = item.get("repository")
            if (
                not sha
                or "git_url" not in item
                or not isinstance(repository, dict)
                or repository.get("private") is not False
            ):
                continue
            row = by_sha.setdefault(sha, {"item": item, "queries": []})
            row["queries"].append(query)

    sources: list[dict] = []
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(_inspect, row["item"], row["queries"]): sha
            for sha, row in by_sha.items()
        }
        for future in as_completed(futures):
            sha = futures[future]
            try:
                sources.append(future.result())
            except Exception as exc:  # keep failures explicit, never count them
                item = by_sha[sha]["item"]
                failures.append(
                    {
                        "sha": sha,
                        "repository": item.get("repository", {}).get("full_name"),
                        "path": item.get("path"),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

    sources.sort(
        key=lambda row: (
            row["repository"].lower(),
            row["path"],
            row["commit_sha"],
            row["blob_sha"],
        )
    )
    failures.sort(key=lambda row: (str(row["repository"]), str(row["path"])))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "method": (
            "GitHub code search via authenticated gh CLI; deduplicated by Git blob SHA; "
            "Git blob API fetch; base64 decode; case-insensitive term screen"
        ),
        "queries": query_rows,
        "successful_blob_count": len(sources),
        "failed_blob_count": len(failures),
        "sources": sources,
        "failures": failures,
    }


def _audit_complete(manifest: dict) -> bool:
    queries = manifest.get("queries", [])
    return bool(
        manifest.get("successful_blob_count", 0) >= 100
        and manifest.get("failed_blob_count") == 0
        and queries
        and all(row.get("returned") == row.get("requested") for row in queries)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/frontier-model-source-manifest-2026-07-28.json"),
    )
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    manifest = build_manifest(args.workers)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"{manifest['successful_blob_count']} inspected blobs; "
        f"{manifest['failed_blob_count']} failures; {args.output}"
    )
    return 0 if _audit_complete(manifest) else 1


if __name__ == "__main__":
    raise SystemExit(main())
