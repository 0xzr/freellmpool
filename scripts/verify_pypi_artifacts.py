#!/usr/bin/env python3
"""Verify local wheel/sdist hashes against an exact PyPI project version."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PYPI_PROJECT_ENDPOINT = "https://pypi.org/pypi"
MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_TIMEOUT_SECONDS = 20.0

_PROJECT_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_PROJECT_SEPARATOR = re.compile(r"[-_.]+")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_WHEEL_BUILD_TAG = re.compile(r"^[0-9][A-Za-z0-9_]*$")
_WHEEL_COMPATIBILITY_TAG = re.compile(r"^[A-Za-z0-9_.]+$")


class ArtifactVerificationError(ValueError):
    """PyPI or the local artifact directory cannot be verified safely."""


class _DuplicateJSONKeyError(ValueError):
    """A JSON object contained an ambiguous duplicate key."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Turn every redirect into a fail-closed HTTP error."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


def normalize_project_name(name: str) -> str:
    """Return the PEP 503 normalized form of a valid PyPI project name."""
    if not isinstance(name, str) or _PROJECT_NAME.fullmatch(name) is None:
        raise ArtifactVerificationError(f"invalid project name: {name!r}")
    return _PROJECT_SEPARATOR.sub("-", name).lower()


def _validate_version(version: str) -> str:
    if (
        not isinstance(version, str)
        or not version
        or len(version) > 200
        or any(ord(character) < 0x21 or ord(character) == 0x7F for character in version)
    ):
        raise ArtifactVerificationError(f"invalid project version: {version!r}")
    return version


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as artifact:
            while chunk := artifact.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ArtifactVerificationError(f"cannot hash local artifact {path.name}: {exc}") from exc
    return digest.hexdigest()


def discover_local_artifacts(dist_dir: Path) -> dict[str, str]:
    """Find and hash exactly one wheel and one gzip source distribution."""
    if not dist_dir.is_dir():
        raise ArtifactVerificationError(f"distribution path is not a directory: {dist_dir}")
    try:
        entries = tuple(dist_dir.iterdir())
    except OSError as exc:
        raise ArtifactVerificationError(f"cannot inspect distribution directory: {exc}") from exc

    wheels = [path for path in entries if path.name.endswith(".whl")]
    sdists = [path for path in entries if path.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1:
        raise ArtifactVerificationError(
            "distribution directory must contain exactly one wheel and one sdist; "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )

    artifacts = (wheels[0], sdists[0])
    for artifact in artifacts:
        if artifact.is_symlink() or not artifact.is_file():
            raise ArtifactVerificationError(
                f"local artifact is not a regular file: {artifact.name}"
            )
    return {artifact.name: _sha256_file(artifact) for artifact in artifacts}


def _validate_local_filenames(
    artifacts: dict[str, str],
    *,
    project: str,
    version: str,
) -> None:
    normalized_project = normalize_project_name(project)
    wheel_project = normalized_project.replace("-", "_")
    wheel = next(filename for filename in artifacts if filename.endswith(".whl"))
    wheel_parts = wheel.removesuffix(".whl").split("-")
    valid_wheel_shape = len(wheel_parts) in {5, 6}
    if valid_wheel_shape:
        distribution, artifact_version = wheel_parts[:2]
        build_tag = wheel_parts[2] if len(wheel_parts) == 6 else None
        compatibility_tags = wheel_parts[-3:]
        valid_wheel_shape = (
            distribution == wheel_project
            and artifact_version == version
            and (build_tag is None or _WHEEL_BUILD_TAG.fullmatch(build_tag) is not None)
            and all(
                _WHEEL_COMPATIBILITY_TAG.fullmatch(tag) is not None
                for tag in compatibility_tags
            )
        )
    if not valid_wheel_shape:
        raise ArtifactVerificationError(
            f"local wheel filename does not match {normalized_project}=={version}: {wheel}"
        )

    sdist = next(filename for filename in artifacts if filename.endswith(".tar.gz"))
    version_suffix = f"-{version}.tar.gz"
    sdist_project = sdist[: -len(version_suffix)] if sdist.endswith(version_suffix) else ""
    if sdist_project not in {normalized_project, wheel_project}:
        raise ArtifactVerificationError(
            f"local sdist filename does not match {normalized_project}=={version}: {sdist}"
        )


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKeyError(key)
        result[key] = value
    return result


def _open_https(request: urllib.request.Request, *, timeout: float) -> Any:
    """Open the already validated fixed-origin HTTPS request."""
    return urllib.request.build_opener(_RejectRedirects()).open(request, timeout=timeout)


def fetch_pypi_version(
    project: str,
    version: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Fetch bounded JSON for one exact PyPI version; return ``None`` on 404."""
    normalized_project = normalize_project_name(project)
    exact_version = _validate_version(version)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ArtifactVerificationError("request timeout must be a positive finite number")

    project_path = urllib.parse.quote(normalized_project, safe="")
    version_path = urllib.parse.quote(exact_version, safe="")
    url = f"{PYPI_PROJECT_ENDPOINT}/{project_path}/{version_path}/json"
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ArtifactVerificationError("PyPI endpoint must use HTTPS")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "freellmpool-release-verifier/1",
        },
    )
    try:
        with _open_https(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status == 404:
                return None
            if status != 200:
                raise ArtifactVerificationError(f"PyPI request returned HTTP {status}")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise ArtifactVerificationError(f"PyPI request returned HTTP {exc.code}") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise ArtifactVerificationError(f"PyPI request failed: {exc}") from exc

    if len(body) > MAX_RESPONSE_BYTES:
        raise ArtifactVerificationError("PyPI response exceeds size limit")
    try:
        payload = json.loads(body, object_pairs_hook=_reject_duplicate_json_keys)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        _DuplicateJSONKeyError,
    ) as exc:
        raise ArtifactVerificationError("PyPI returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ArtifactVerificationError("PyPI response must be a JSON object")
    return payload


def _parse_remote_artifacts(
    payload: dict[str, Any],
    *,
    project: str,
    version: str,
) -> dict[str, str]:
    info = payload.get("info")
    if not isinstance(info, dict):
        raise ArtifactVerificationError("PyPI response is missing info metadata")
    remote_name = info.get("name")
    if not isinstance(remote_name, str):
        raise ArtifactVerificationError("PyPI response has an invalid project name")
    try:
        normalized_remote_name = normalize_project_name(remote_name)
    except ArtifactVerificationError as exc:
        raise ArtifactVerificationError("PyPI response has an invalid project name") from exc
    if normalized_remote_name != normalize_project_name(project):
        raise ArtifactVerificationError("PyPI response project name does not match request")
    if info.get("version") != version:
        raise ArtifactVerificationError("PyPI response version metadata does not match request")

    urls = payload.get("urls")
    if not isinstance(urls, list):
        raise ArtifactVerificationError("PyPI response has no urls list")

    artifacts: dict[str, str] = {}
    for index, entry in enumerate(urls):
        if not isinstance(entry, dict):
            raise ArtifactVerificationError(f"PyPI urls entry {index} is not an object")
        filename = entry.get("filename")
        if (
            not isinstance(filename, str)
            or not filename
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or "\x00" in filename
        ):
            raise ArtifactVerificationError(f"PyPI urls entry {index} has an invalid filename")
        if filename in artifacts:
            raise ArtifactVerificationError(f"PyPI response has duplicate filename: {filename}")

        digests = entry.get("digests")
        if not isinstance(digests, dict) or "sha256" not in digests:
            raise ArtifactVerificationError(f"PyPI artifact {filename} is missing sha256")
        sha256 = digests["sha256"]
        if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
            raise ArtifactVerificationError(f"PyPI artifact {filename} has invalid sha256")
        artifacts[filename] = sha256.lower()
    return artifacts


def verify_artifact_sets(
    local: dict[str, str],
    remote: dict[str, str],
    *,
    mode: str,
    version_found: bool,
) -> None:
    """Compare PyPI filenames/hashes under the retry-safe or exact policy."""
    if mode not in {"subset", "exact"}:
        raise ArtifactVerificationError(f"invalid verification mode: {mode!r}")
    if not version_found and remote:
        raise ArtifactVerificationError("unpublished version cannot contain remote artifacts")
    if mode == "exact" and not version_found:
        raise ArtifactVerificationError("PyPI version is not published")

    extras = sorted(set(remote) - set(local))
    if extras:
        raise ArtifactVerificationError(
            "unexpected remote filename(s): " + ", ".join(extras)
        )
    for filename, remote_hash in remote.items():
        if not hmac.compare_digest(local[filename], remote_hash):
            raise ArtifactVerificationError(f"hash mismatch for {filename}")

    if mode == "exact" and set(remote) != set(local):
        raise ArtifactVerificationError(
            "published artifact set does not exactly match local wheel and sdist"
        )


def verify_pypi_artifacts(
    dist_dir: Path,
    *,
    project: str,
    version: str,
    mode: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, int]:
    """Verify a local release pair and return ``(remote_count, local_count)``."""
    normalized_project = normalize_project_name(project)
    exact_version = _validate_version(version)
    local = discover_local_artifacts(dist_dir)
    _validate_local_filenames(
        local,
        project=normalized_project,
        version=exact_version,
    )
    payload = fetch_pypi_version(normalized_project, exact_version, timeout=timeout)
    version_found = payload is not None
    remote = (
        _parse_remote_artifacts(
            payload,
            project=normalized_project,
            version=exact_version,
        )
        if payload is not None
        else {}
    )
    verify_artifact_sets(local, remote, mode=mode, version_found=version_found)
    return len(remote), len(local)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", help="PyPI project name")
    parser.add_argument("version", help="Exact project version")
    parser.add_argument("dist_dir", type=Path, help="directory containing one wheel and sdist")
    parser.add_argument("--mode", choices=("subset", "exact"), default="exact")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    try:
        remote_count, local_count = verify_pypi_artifacts(
            args.dist_dir,
            project=args.project,
            version=args.version,
            mode=args.mode,
            timeout=args.timeout,
        )
    except ArtifactVerificationError as exc:
        print(f"PyPI artifact verification failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"PyPI artifacts verified {args.mode} ({remote_count}/{local_count}) "
        f"for {normalize_project_name(args.project)}=={args.version}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
