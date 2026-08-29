"""Exercise an installed llm-freellmpool entry point without network access."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

EXPECTED_OUTPUT = "llm-freellmpool mocked smoke passed"
STABLE_TARGET = "groq/openai/gpt-oss-20b"

SITECUSTOMIZE = f"""\
import socket
from types import SimpleNamespace


def _block_network(*args, **kwargs):
    raise AssertionError("network access attempted during llm plugin smoke")


socket.create_connection = _block_network
socket.socket.connect = _block_network

import freellmpool


class _SmokePool:
    @classmethod
    def from_default_config(cls):
        return cls()

    def chat(self, messages, *, model, providers):
        assert messages == [{{"role": "user", "content": "plugin smoke"}}]
        assert model == "openai/gpt-oss-20b"
        assert providers == ["groq"]
        return SimpleNamespace(
            text={EXPECTED_OUTPUT!r},
            provider_id="groq",
            model=model,
        )


freellmpool.Pool = _SmokePool
"""


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: smoke_cli.py /path/to/llm")
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise SystemExit(f"llm executable not found: {executable}")

    with tempfile.TemporaryDirectory(prefix="llm-freellmpool-smoke-") as temporary:
        temporary_path = Path(temporary)
        (temporary_path / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "LLM_USER_PATH": temporary,
                "PYTHONPATH": temporary,
                "XDG_CACHE_HOME": temporary,
                "XDG_CONFIG_HOME": temporary,
                "XDG_DATA_HOME": temporary,
            }
        )
        completed = subprocess.run(
            [
                str(executable),
                "-m",
                "freellmpool",
                "-o",
                "target",
                STABLE_TARGET,
                "plugin smoke",
            ],
            cwd=temporary,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    if completed.returncode:
        sys.stderr.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        return completed.returncode
    if EXPECTED_OUTPUT not in completed.stdout:
        sys.stderr.write(f"unexpected llm output: {completed.stdout!r}\n")
        return 1
    print(EXPECTED_OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
