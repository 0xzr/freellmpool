"""Offline validation for the reproducible GitHub frontier-source manifest."""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "audit_frontier_sources",
    Path(__file__).parents[1] / "scripts" / "audit_frontier_sources.py",
)
assert _SPEC is not None and _SPEC.loader is not None
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)

_COMMIT = "1" * 40
_BLOB = "2" * 40


def _item(*, html_commit: str = _COMMIT) -> dict:
    return {
        "path": "README.md",
        "git_url": f"https://api.github.com/repositories/1/git/blobs/{_BLOB}",
        "url": f"https://api.github.com/repos/acme/project/contents/README.md?ref={_COMMIT}",
        "html_url": f"https://github.com/acme/project/blob/{html_commit}/README.md",
        "repository": {"full_name": "acme/project", "private": False},
    }


def test_inspect_records_commit_and_blob_shas_with_valid_immutable_links(monkeypatch):
    monkeypatch.setattr(
        audit,
        "_gh_json",
        lambda _url: {
            "sha": _BLOB,
            "content": base64.b64encode(b"GLM-5.2 provider").decode(),
        },
    )

    row = audit._inspect(_item(), ["GLM-5.2"])

    assert row["commit_sha"] == _COMMIT
    assert row["blob_sha"] == _BLOB
    assert f"/blob/{_COMMIT}/" in row["immutable_url"]
    assert row["blob_api_url"].endswith(_BLOB)


def test_inspect_rejects_web_link_not_pinned_to_recorded_commit(monkeypatch):
    monkeypatch.setattr(
        audit,
        "_gh_json",
        lambda _url: {"sha": _BLOB, "content": base64.b64encode(b"x").decode()},
    )

    with pytest.raises(ValueError, match="html_url"):
        audit._inspect(_item(html_commit="3" * 40), ["GLM-5.2"])


@pytest.mark.parametrize(
    "changes",
    [
        {"failed_blob_count": 1},
        {"successful_blob_count": 99},
        {"queries": [{"requested": 30, "returned": 29}]},
    ],
)
def test_recorded_audit_requires_full_queries_zero_failures_and_100_sources(changes):
    manifest = {
        "successful_blob_count": 100,
        "failed_blob_count": 0,
        "queries": [{"requested": 30, "returned": 30}],
        **changes,
    }

    assert audit._audit_complete(manifest) is False
