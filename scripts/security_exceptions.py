#!/usr/bin/env python3
"""Validate and render the repository's time-bounded scanner exceptions."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tokenize
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = ROOT / ".github" / "security-exceptions.json"
SCANNERS = frozenset({"pip-audit", "trivy"})
_FIELDS = frozenset(
    {"id", "scanner", "justification", "owner", "expires", "issue"}
)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}")
_OWNER = re.compile(r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_ISSUE = re.compile(
    r"https://github\.com/0xzr/freellmpool/"
    r"(?:issues/[1-9][0-9]*|security/advisories/GHSA-[A-Za-z0-9-]+)"
)
_MAX_POLICY_BYTES = 256_000
_PYTHON_SUPPRESSION_PATTERNS = (
    ("Bandit", re.compile(r"#\s*nosec(?:\s|$)", re.IGNORECASE)),
    ("CodeQL", re.compile(r"#\s*(?:lgtm|codeql)\s*\[", re.IGNORECASE)),
)
_YAML_SUPPRESSION_PATTERNS = (
    ("zizmor", re.compile(r"#\s*zizmor:\s*ignore", re.IGNORECASE)),
    ("CodeQL", re.compile(r"#\s*(?:lgtm|codeql)\s*\[", re.IGNORECASE)),
)
_IGNORED_TREE_PARTS = frozenset(
    {
        ".codegraph",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "vendor",
        "venv",
    }
)


class ExceptionPolicyError(ValueError):
    """The exception registry is malformed, expired, or too permissive."""


@dataclass(frozen=True)
class SecurityException:
    id: str
    scanner: str
    justification: str
    owner: str
    expires: date
    issue: str


def load_exceptions(
    path: Path = DEFAULT_POLICY,
    *,
    today: date | None = None,
) -> tuple[SecurityException, ...]:
    """Load a strict, bounded registry and reject expired entries."""
    today = today or datetime.now(UTC).date()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ExceptionPolicyError(f"cannot read exception policy: {exc}") from exc
    if len(raw) > _MAX_POLICY_BYTES:
        raise ExceptionPolicyError("exception policy exceeds size limit")
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ExceptionPolicyError(f"invalid exception policy JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExceptionPolicyError("exception policy must be an object")
    if set(payload) != {"schema_version", "exceptions"}:
        raise ExceptionPolicyError("exception policy has unknown or missing fields")
    if payload["schema_version"] != 1:
        raise ExceptionPolicyError("unsupported exception policy schema_version")
    entries = payload["exceptions"]
    if not isinstance(entries, list):
        raise ExceptionPolicyError("exceptions must be a list")

    result: list[SecurityException] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        prefix = f"exceptions[{index}]"
        if not isinstance(entry, dict) or set(entry) != _FIELDS:
            raise ExceptionPolicyError(f"{prefix} has unknown or missing fields")
        values = {key: entry[key] for key in _FIELDS}
        if not all(isinstance(value, str) for value in values.values()):
            raise ExceptionPolicyError(f"{prefix} fields must all be strings")
        scanner = values["scanner"]
        if scanner not in SCANNERS:
            raise ExceptionPolicyError(f"{prefix} scanner is unsupported")
        exception_id = values["id"]
        if _ID.fullmatch(exception_id) is None:
            raise ExceptionPolicyError(f"{prefix} id is invalid")
        justification = values["justification"].strip()
        if not 20 <= len(justification) <= 1_000:
            raise ExceptionPolicyError(
                f"{prefix} justification must contain 20-1000 characters"
            )
        owner = values["owner"]
        if _OWNER.fullmatch(owner) is None:
            raise ExceptionPolicyError(f"{prefix} owner must be a GitHub handle")
        issue = values["issue"]
        if _ISSUE.fullmatch(issue) is None:
            raise ExceptionPolicyError(
                f"{prefix} issue must link to this repository's issue tracker"
            )
        try:
            expires = date.fromisoformat(values["expires"])
        except ValueError as exc:
            raise ExceptionPolicyError(
                f"{prefix} expires must be an ISO date"
            ) from exc
        if expires < today:
            raise ExceptionPolicyError(
                f"{prefix} expired on {expires.isoformat()}"
            )
        if (expires - today).days > 90:
            raise ExceptionPolicyError(
                f"{prefix} expires more than 90 days in the future"
            )
        identity = (scanner, exception_id)
        if identity in seen:
            raise ExceptionPolicyError(f"{prefix} duplicates {scanner}/{exception_id}")
        seen.add(identity)
        result.append(
            SecurityException(
                id=exception_id,
                scanner=scanner,
                justification=justification,
                owner=owner,
                expires=expires,
                issue=issue,
            )
        )
    return tuple(result)


def find_native_suppressions(root: Path = ROOT) -> tuple[str, ...]:
    """Find in-repository directives that would bypass mandatory scanners."""
    findings: list[str] = []
    python_paths = sorted(
        path
        for path in root.rglob("*.py")
        if not _IGNORED_TREE_PARTS.intersection(path.relative_to(root).parts)
    )
    for path in python_paths:
        try:
            with path.open(encoding="utf-8") as handle:
                comments = (
                    token
                    for token in tokenize.generate_tokens(handle.readline)
                    if token.type == tokenize.COMMENT
                )
                for token in comments:
                    for scanner, pattern in _PYTHON_SUPPRESSION_PATTERNS:
                        if pattern.search(token.string):
                            relative = path.relative_to(root).as_posix()
                            findings.append(
                                f"{relative}:{token.start[0]}: {scanner} suppression"
                            )
        except (OSError, UnicodeError, tokenize.TokenError) as exc:
            raise ExceptionPolicyError(f"cannot inspect {path}: {exc}") from exc

    yaml_paths = [
        *sorted((root / ".github").rglob("*.yml")),
        *sorted((root / ".github").rglob("*.yaml")),
        *(path for path in (root / "zizmor.yml", root / "zizmor.yaml") if path.is_file()),
    ]
    for path in yaml_paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise ExceptionPolicyError(f"cannot inspect {path}: {exc}") from exc
        for line_number, line in enumerate(lines, 1):
            for scanner, pattern in _YAML_SUPPRESSION_PATTERNS:
                if pattern.search(line):
                    relative = path.relative_to(root).as_posix()
                    findings.append(
                        f"{relative}:{line_number}: {scanner} suppression"
                    )
    return tuple(sorted(findings))


def ids_for(
    exceptions: Iterable[SecurityException],
    scanner: str,
) -> tuple[str, ...]:
    if scanner not in SCANNERS:
        raise ExceptionPolicyError(f"unsupported scanner: {scanner}")
    return tuple(
        sorted(entry.id for entry in exceptions if entry.scanner == scanner)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_POLICY,
        help="exception registry (default: .github/security-exceptions.json)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate the complete registry")
    subparsers.add_parser(
        "check-suppressions",
        help="reject native source and workflow suppression directives",
    )
    ids_parser = subparsers.add_parser("ids", help="print active IDs for one scanner")
    ids_parser.add_argument("scanner", choices=sorted(SCANNERS))
    args = parser.parse_args(argv)
    try:
        exceptions = load_exceptions(args.file)
    except ExceptionPolicyError as exc:
        print(f"security exception policy failed: {exc}", file=sys.stderr)
        return 2
    if args.command == "ids":
        for exception_id in ids_for(exceptions, args.scanner):
            print(exception_id)
    elif args.command == "check-suppressions":
        try:
            findings = find_native_suppressions()
        except ExceptionPolicyError as exc:
            print(f"security suppression check failed: {exc}", file=sys.stderr)
            return 2
        if findings:
            for finding in findings:
                print(finding, file=sys.stderr)
            return 2
        print("Security suppression check passed: none found.")
    else:
        print(f"Security exception policy passed: {len(exceptions)} active.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
