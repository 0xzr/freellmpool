from __future__ import annotations

import hashlib
import http.client
import io
import json
import urllib.error
from pathlib import Path

import pytest

import scripts.verify_pypi_artifacts as verifier
from scripts.verify_pypi_artifacts import (
    ArtifactVerificationError,
    discover_local_artifacts,
    fetch_pypi_version,
    normalize_project_name,
    verify_artifact_sets,
    verify_pypi_artifacts,
)

PROJECT = "Example.Project_Name"
NORMALIZED_PROJECT = "example-project-name"
VERSION = "1.2.3"
WHEEL = "example_project_name-1.2.3-py3-none-any.whl"
SDIST = "example_project_name-1.2.3.tar.gz"


def _write_artifacts(path: Path) -> dict[str, str]:
    contents = {WHEEL: b"wheel contents", SDIST: b"sdist contents"}
    for filename, body in contents.items():
        (path / filename).write_bytes(body)
    return {
        filename: hashlib.sha256(body).hexdigest()
        for filename, body in contents.items()
    }


def _payload(hashes: dict[str, str], filenames: list[str] | None = None) -> dict:
    selected = filenames if filenames is not None else list(hashes)
    return {
        "info": {"name": "Example.Project-Name", "version": VERSION},
        "urls": [
            {"filename": filename, "digests": {"sha256": hashes[filename]}}
            for filename in selected
        ],
    }


class _Response:
    def __init__(self, body: bytes, *, status: int = 200):
        self._body = io.BytesIO(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


def test_normalize_project_name_uses_pep_503_rules():
    assert normalize_project_name(PROJECT) == NORMALIZED_PROJECT
    assert normalize_project_name("A..b___C---d") == "a-b-c-d"


@pytest.mark.parametrize("name", ["", ".bad", "bad-", "bad/name", "caf\N{LATIN SMALL LETTER E WITH ACUTE}"])
def test_normalize_project_name_rejects_invalid_names(name):
    with pytest.raises(ArtifactVerificationError, match="invalid project name"):
        normalize_project_name(name)


def test_discover_local_artifacts_hashes_exactly_one_wheel_and_sdist(tmp_path):
    expected = _write_artifacts(tmp_path)
    (tmp_path / "SHA256SUMS").write_text("ignored sidecar", encoding="utf-8")

    assert discover_local_artifacts(tmp_path) == expected


@pytest.mark.parametrize(
    "extra_name",
    [
        "other-1.2.3-py3-none-any.whl",
        "other-1.2.3.tar.gz",
    ],
)
def test_discover_local_artifacts_rejects_duplicate_artifact_types(tmp_path, extra_name):
    _write_artifacts(tmp_path)
    (tmp_path / extra_name).write_bytes(b"unexpected")

    with pytest.raises(ArtifactVerificationError, match="exactly one wheel and one sdist"):
        discover_local_artifacts(tmp_path)


def test_discover_local_artifacts_rejects_missing_or_invalid_directory(tmp_path):
    with pytest.raises(ArtifactVerificationError, match="exactly one wheel and one sdist"):
        discover_local_artifacts(tmp_path)
    with pytest.raises(ArtifactVerificationError, match="not a directory"):
        discover_local_artifacts(tmp_path / "missing")


@pytest.mark.parametrize(
    ("wheel", "sdist", "message"),
    [
        (
            "other-1.2.3-py3-none-any.whl",
            "other-1.2.3.tar.gz",
            "wheel filename",
        ),
        (
            "example_project_name-9.9.9-py3-none-any.whl",
            "example_project_name-9.9.9.tar.gz",
            "wheel filename",
        ),
        (
            WHEEL,
            "other-1.2.3.tar.gz",
            "sdist filename",
        ),
        (
            "example_project_name-1.2.3-invalid.whl",
            SDIST,
            "wheel filename",
        ),
    ],
)
def test_subset_404_rejects_artifacts_not_bound_to_project_version(
    tmp_path, monkeypatch, wheel, sdist, message
):
    (tmp_path / wheel).write_bytes(b"wheel")
    (tmp_path / sdist).write_bytes(b"sdist")
    monkeypatch.setattr(
        verifier,
        "fetch_pypi_version",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(ArtifactVerificationError, match=message):
        verify_pypi_artifacts(
            tmp_path,
            project=PROJECT,
            version=VERSION,
            mode="subset",
        )


def test_verify_artifact_sets_accepts_exact_and_hash_matching_subsets(tmp_path):
    local = _write_artifacts(tmp_path)

    verify_artifact_sets(local, local, mode="exact", version_found=True)
    verify_artifact_sets(
        local,
        {WHEEL: local[WHEEL]},
        mode="subset",
        version_found=True,
    )
    verify_artifact_sets(local, {}, mode="subset", version_found=False)


def test_verify_artifact_sets_requires_published_version_in_exact_mode(tmp_path):
    local = _write_artifacts(tmp_path)

    with pytest.raises(ArtifactVerificationError, match="version is not published"):
        verify_artifact_sets(local, {}, mode="exact", version_found=False)
    with pytest.raises(ArtifactVerificationError, match="does not exactly match"):
        verify_artifact_sets(
            local,
            {WHEEL: local[WHEEL]},
            mode="exact",
            version_found=True,
        )


@pytest.mark.parametrize(
    ("remote", "message"),
    [
        ({"unexpected.whl": "0" * 64}, "unexpected remote filename"),
        ({WHEEL: "0" * 64}, "hash mismatch"),
    ],
)
def test_verify_artifact_sets_rejects_remote_extras_and_hash_drift(
    tmp_path, remote, message
):
    local = _write_artifacts(tmp_path)

    with pytest.raises(ArtifactVerificationError, match=message):
        verify_artifact_sets(local, remote, mode="subset", version_found=True)


@pytest.mark.parametrize("mode", ["invalid", ""])
def test_verify_artifact_sets_rejects_unknown_mode(tmp_path, mode):
    local = _write_artifacts(tmp_path)
    with pytest.raises(ArtifactVerificationError, match="invalid verification mode"):
        verify_artifact_sets(local, {}, mode=mode, version_found=False)


def test_fetch_pypi_version_uses_normalized_exact_version_url(monkeypatch):
    payload = {"info": {"name": PROJECT, "version": VERSION}, "urls": []}
    requests = []

    def fake_urlopen(request, *, timeout):
        requests.append((request, timeout))
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(verifier, "_open_https", fake_urlopen)

    assert fetch_pypi_version(PROJECT, VERSION, timeout=7.5) == payload
    request, timeout = requests[0]
    assert request.full_url == (
        "https://pypi.org/pypi/example-project-name/1.2.3/json"
    )
    assert request.get_header("Accept") == "application/json"
    assert timeout == 7.5


def test_https_opener_refuses_redirects(monkeypatch):
    captured = []

    class FakeOpener:
        def open(self, request, *, timeout):
            return request, timeout

    def fake_build_opener(*handlers):
        captured.extend(handlers)
        return FakeOpener()

    monkeypatch.setattr(verifier.urllib.request, "build_opener", fake_build_opener)
    request = verifier.urllib.request.Request("https://pypi.org/pypi/demo/1/json")

    assert verifier._open_https(request, timeout=3.0) == (request, 3.0)
    assert len(captured) == 1
    assert isinstance(captured[0], verifier._RejectRedirects)
    assert captured[0].redirect_request(request, None, 302, "redirect", {}, "https://bad") is None


def test_fetch_pypi_version_accepts_authoritative_404(monkeypatch):
    def not_found(request, *, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

    monkeypatch.setattr(verifier, "_open_https", not_found)

    assert fetch_pypi_version(PROJECT, VERSION) is None


def test_fetch_pypi_version_accepts_404_response_object(monkeypatch):
    monkeypatch.setattr(
        verifier,
        "_open_https",
        lambda _request, *, timeout: _Response(b"", status=404),
    )

    assert fetch_pypi_version(PROJECT, VERSION) is None


@pytest.mark.parametrize("status", [301, 403, 429, 500, 503])
def test_fetch_pypi_version_rejects_all_non_404_http_errors(monkeypatch, status):
    def failed(request, *, timeout):
        raise urllib.error.HTTPError(request.full_url, status, "failure", {}, None)

    monkeypatch.setattr(verifier, "_open_https", failed)

    with pytest.raises(ArtifactVerificationError, match=f"HTTP {status}"):
        fetch_pypi_version(PROJECT, VERSION)


@pytest.mark.parametrize(
    "failure",
    [urllib.error.URLError("offline"), http.client.RemoteDisconnected("disconnected")],
)
def test_fetch_pypi_version_rejects_network_failures(monkeypatch, failure):
    def failed(_request, *, timeout):
        raise failure

    monkeypatch.setattr(verifier, "_open_https", failed)

    with pytest.raises(ArtifactVerificationError, match="request failed"):
        fetch_pypi_version(PROJECT, VERSION)


@pytest.mark.parametrize(
    "body",
    [
        b"not JSON",
        b"null",
        b"[]",
        b'{"info":{},"info":{}}',
        b"[" * 2_000 + b"]" * 2_000,
    ],
)
def test_fetch_pypi_version_rejects_malformed_json(monkeypatch, body):
    monkeypatch.setattr(
        verifier,
        "_open_https",
        lambda _request, *, timeout: _Response(body),
    )

    with pytest.raises(ArtifactVerificationError, match="invalid JSON|JSON object"):
        fetch_pypi_version(PROJECT, VERSION)


def test_fetch_pypi_version_rejects_oversize_response(monkeypatch):
    body = b" " * (verifier.MAX_RESPONSE_BYTES + 1)
    monkeypatch.setattr(
        verifier,
        "_open_https",
        lambda _request, *, timeout: _Response(body),
    )

    with pytest.raises(ArtifactVerificationError, match="exceeds size limit"):
        fetch_pypi_version(PROJECT, VERSION)


def test_fetch_pypi_version_rejects_non_success_response_object(monkeypatch):
    monkeypatch.setattr(
        verifier,
        "_open_https",
        lambda _request, *, timeout: _Response(b"{}", status=403),
    )

    with pytest.raises(ArtifactVerificationError, match="HTTP 403"):
        fetch_pypi_version(PROJECT, VERSION)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.pop("info"), "missing info"),
        (lambda payload: payload["info"].update(name="other"), "project name"),
        (lambda payload: payload["info"].update(version="9.9"), "version metadata"),
        (lambda payload: payload.update(urls={}), "urls list"),
        (lambda payload: payload["urls"].append(payload["urls"][0]), "duplicate filename"),
        (lambda payload: payload["urls"][0].pop("filename"), "invalid filename"),
        (lambda payload: payload["urls"][0].update(filename="../bad.whl"), "invalid filename"),
        (lambda payload: payload["urls"][0].pop("digests"), "missing sha256"),
        (
            lambda payload: payload["urls"][0]["digests"].update(sha256="xyz"),
            "invalid sha256",
        ),
    ],
)
def test_verify_pypi_artifacts_rejects_malformed_or_ambiguous_metadata(
    tmp_path, monkeypatch, mutate, message
):
    hashes = _write_artifacts(tmp_path)
    payload = _payload(hashes)
    mutate(payload)
    monkeypatch.setattr(verifier, "fetch_pypi_version", lambda *_args, **_kwargs: payload)

    with pytest.raises(ArtifactVerificationError, match=message):
        verify_pypi_artifacts(
            tmp_path,
            project=PROJECT,
            version=VERSION,
            mode="subset",
        )


def test_verify_pypi_artifacts_end_to_end_exact(tmp_path, monkeypatch):
    hashes = _write_artifacts(tmp_path)
    monkeypatch.setattr(
        verifier,
        "fetch_pypi_version",
        lambda *_args, **_kwargs: _payload(hashes),
    )

    result = verify_pypi_artifacts(
        tmp_path,
        project=PROJECT,
        version=VERSION,
        mode="exact",
    )

    assert result == (2, 2)


def test_verify_pypi_artifacts_subset_accepts_404(tmp_path, monkeypatch):
    _write_artifacts(tmp_path)
    monkeypatch.setattr(
        verifier,
        "fetch_pypi_version",
        lambda *_args, **_kwargs: None,
    )

    assert verify_pypi_artifacts(
        tmp_path,
        project=PROJECT,
        version=VERSION,
        mode="subset",
    ) == (0, 2)


def test_main_reports_success_and_concise_failure(tmp_path, monkeypatch, capsys):
    _write_artifacts(tmp_path)
    monkeypatch.setattr(
        verifier,
        "fetch_pypi_version",
        lambda *_args, **_kwargs: None,
    )
    args = [
        PROJECT,
        VERSION,
        str(tmp_path),
        "--mode",
        "subset",
    ]

    assert verifier.main(args) == 0
    assert "verified subset (0/2)" in capsys.readouterr().out

    monkeypatch.setattr(
        verifier,
        "verify_pypi_artifacts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ArtifactVerificationError("deliberate failure")
        ),
    )
    assert verifier.main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "PyPI artifact verification failed: deliberate failure\n"
