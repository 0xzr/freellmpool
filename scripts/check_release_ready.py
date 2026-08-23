#!/usr/bin/env python3
"""Release readiness checks for freellmpool.

By default this validates metadata/count surfaces and then builds, checks, and
fresh-installs the wheel from the current checkout. Use ``--skip-build`` for a
fast CI metadata guard.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from catalog_counts import catalog_counts  # noqa: E402

from freellmpool._version import __version__  # noqa: E402

# Public commands that WU-001 through WU-011 introduced. This release check makes
# sure the CLI still advertises every surface we document.
_EXPECTED_PUBLIC_COMMANDS = {
    "ask",
    "roles",
    "tokenmax",
    "battle",
    "playground",
    "recipe",
    "jobs",
    "report",
    "cost",
    "providers",
    "models",
    "quota",
    "quota-wise",
    "stats",
    "badge",
    "keys",
    "catalog",
    "capability",
    "capacity",
    "benchmark",
    "doctor",
    "proxy",
    "tailnet",
    "init",
    "profile",
    "mcp",
    "code",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_pyproject(root: Path) -> dict:
    return tomllib.loads(_read(root / "pyproject.toml"))


def metadata_errors(root: Path, *, version: str | None = None) -> list[str]:
    version = version or __version__
    errors: list[str] = []
    pyproject = _load_pyproject(root)
    project = pyproject["project"]
    server = json.loads(_read(root / "server.json"))
    readme = _read(root / "README.md")
    docs_index = _read(root / "docs" / "index.html")
    demo = _read(root / "assets" / "demo.svg")
    changelog = _read(root / "CHANGELOG.md")
    demo_script = _read(root / "scripts" / "demo.sh")
    counts = catalog_counts(root)

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(project["version"] == version, f"pyproject version is {project['version']}, not {version}")
    require(__version__ == version, f"package __version__ is {__version__}, not {version}")
    require(server.get("version") == version, "server.json top-level version mismatch")
    package_version = ((server.get("packages") or [{}])[0] or {}).get("version")
    require(package_version == version, "server.json package version mismatch")
    require(f"Latest release: {version}" in docs_index, "docs/index.html latest release mismatch")
    require(
        f'"softwareVersion": "{version}"' in docs_index,
        "docs/index.html JSON-LD softwareVersion mismatch",
    )
    require(f"freellmpool-{version}" in demo, "assets/demo.svg install transcript mismatch")
    require(re.search(rf"^## \[{re.escape(version)}\]", changelog, re.MULTILINE) is not None, "CHANGELOG missing top-level release entry")

    provider_phrase = f"{counts.providers} LLM providers"
    require(provider_phrase in project["description"], "pyproject provider count mismatch")
    require(provider_phrase in server["description"], "server.json provider count mismatch")
    require(provider_phrase in readme, "README provider count mismatch")
    require(f"{counts.providers} cataloged providers" in demo, "demo SVG provider count mismatch")
    require(
        f"{counts.enabled_chat_models} enabled routes" in demo,
        "demo SVG enabled route count mismatch",
    )
    require(
        f"{counts.enabled_chat_models} enabled chat routes" in readme,
        "README enabled route bucket mismatch",
    )
    require(
        f"{counts.enabled_chat_models} enabled chat routes" in docs_index,
        "docs/index.html enabled route bucket mismatch",
    )
    require(
        f"{counts.cataloged_chat_models} cataloged" in project["description"],
        "pyproject cataloged model bucket mismatch",
    )
    require(
        f"{counts.cataloged_chat_models} cataloged" in readme,
        "README cataloged model bucket mismatch",
    )
    require(
        f"{counts.cataloged_chat_models} cataloged" in docs_index,
        "docs/index.html cataloged model bucket mismatch",
    )
    require("16 providers, 56 models" not in demo_script, "scripts/demo.sh has stale proxy count")

    # stdlib-first runtime contract: only httpx is a required runtime dependency.
    runtime_deps = project.get("dependencies", [])
    allowed_runtime = ["httpx>=0.27"]
    if runtime_deps != allowed_runtime:
        errors.append(
            "stdlib-first contract: project.dependencies must be exactly "
            f"{allowed_runtime!r}. Add an explicit architecture note before changing."
        )

    errors.extend(_public_command_errors(root))

    return errors


def _public_command_errors(root: Path) -> list[str]:
    """Verify the CLI advertises all the public commands documented for release."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [sys.executable, "-m", "freellmpool", "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        return [f"freellmpool --help failed: {result.stderr.strip()}"]
    choice_block = re.search(r"\{([^}]+)\}", result.stdout)
    if choice_block is None:
        return ["Could not find command choice block in freellmpool --help"]
    commands = {c.strip() for c in choice_block.group(1).split(",")}
    missing = sorted(_EXPECTED_PUBLIC_COMMANDS - commands)
    if missing:
        return [f"CLI is missing documented public commands: {missing}"]
    return []


def _run(cmd: list[str], *, cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def build_and_smoke(root: Path, *, version: str, dist_dir: Path | None = None) -> None:
    if dist_dir is None:
        tmp = tempfile.TemporaryDirectory(prefix="flp-release-ready-")
        dist = Path(tmp.name) / "dist"
    else:
        tmp = None
        dist = dist_dir
        if dist.exists():
            shutil.rmtree(dist)

    # The host toolchain may ship an old pkginfo/twine that rejects modern
    # metadata. Create an isolated checker venv with
    # packaging tools known to support modern metadata.
    checker_tmp = tempfile.TemporaryDirectory(prefix="flp-release-checker-")
    checker = Path(checker_tmp.name) / "venv"

    try:
        dist.mkdir(parents=True, exist_ok=True)

        _run([sys.executable, "-m", "venv", str(checker)], cwd=root)
        checker_py = checker / "bin" / "python"
        _run([str(checker_py), "-m", "pip", "install", "--upgrade", "pip"], cwd=root)
        _run(
            [
                str(checker_py),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "build>=1.2",
                "twine==7.0.0",
                "pkginfo>=1.12,<2.0",
            ],
            cwd=root,
        )

        _run([str(checker_py), "-m", "build", "--outdir", str(dist)], cwd=root)
        _run(
            [
                str(checker_py),
                "-m",
                "twine",
                "check",
                *[str(p) for p in sorted(dist.iterdir())],
            ],
            cwd=root,
        )

        wheels = sorted(dist.glob("*.whl"))
        if not wheels:
            raise RuntimeError("build produced no wheel")

        venv = dist.parent / "wheel-smoke"
        if venv.exists():
            shutil.rmtree(venv)
        _run([sys.executable, "-m", "venv", str(venv)], cwd=root)
        py = venv / "bin" / "python"
        cli = venv / "bin" / "freellmpool"
        _run([str(py), "-m", "pip", "install", "--upgrade", "pip"], cwd=root)
        _run([str(py), "-m", "pip", "install", "--no-cache-dir", str(wheels[0])], cwd=root)
        check = (
            "import freellmpool; "
            f"assert freellmpool.__version__ == {version!r}, freellmpool.__version__; "
            "from freellmpool import client; "
            f"assert 'freellmpool/{version}' in client._USER_AGENT, client._USER_AGENT"
        )
        _run([str(py), "-c", check], cwd=root)
        _run([str(cli), "--version"], cwd=root)
    finally:
        if tmp is not None:
            tmp.cleanup()
        checker_tmp.cleanup()


def pypi_smoke(version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="flp-pypi-smoke-") as td:
        root = Path(td)
        venv = root / "venv"
        _run([sys.executable, "-m", "venv", str(venv)], cwd=root)
        py = venv / "bin" / "python"
        cli = venv / "bin" / "freellmpool"
        _run([str(py), "-m", "pip", "install", "--upgrade", "pip"], cwd=root)
        _run([str(py), "-m", "pip", "install", "--no-cache-dir", f"freellmpool=={version}"], cwd=root)
        _run([str(py), "-c", f"import freellmpool; assert freellmpool.__version__ == {version!r}"], cwd=root)
        _run([str(cli), "--version"], cwd=root)


def docker_smoke(image: str, version: str) -> None:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker is not installed")
    _run([docker, "pull", image], cwd=Path.cwd())
    _run([docker, "run", "--rm", image, "--version"], cwd=Path.cwd())
    # Keep this smoke cheap and non-networked inside the container.
    _run(
        [
            docker,
            "run",
            "--rm",
            "-e",
            "FREELLMPOOL_CONFIG_FILE=/tmp/config.toml",
            "-e",
            "FREELLMPOOL_QUOTA_PATH=/tmp/quota.json",
            image,
            "doctor",
        ],
        cwd=Path.cwd(),
    )
    print(f"Docker image {image} smoke passed for {version}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--version", default=__version__)
    parser.add_argument("--skip-build", action="store_true", help="only run metadata checks")
    parser.add_argument("--dist-dir", type=Path, help="where to write build artifacts")
    parser.add_argument("--check-pypi", action="store_true", help="install this version from PyPI")
    parser.add_argument("--check-docker", metavar="IMAGE", help="pull and smoke-test a Docker image")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    errors = metadata_errors(root, version=args.version)
    if errors:
        print("Release readiness failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"Metadata readiness passed for freellmpool {args.version}.")
    if not args.skip_build:
        build_and_smoke(root, version=args.version, dist_dir=args.dist_dir)
        print("Build, twine check, and fresh wheel smoke passed.")
    if args.check_pypi:
        pypi_smoke(args.version)
        print(f"PyPI smoke passed for freellmpool {args.version}.")
    if args.check_docker:
        docker_smoke(args.check_docker, args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
