from __future__ import annotations

import tomllib
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
ROOT = PLUGIN_DIR.parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
PUBLISH = ROOT / ".github" / "workflows" / "publish-llm-plugin.yml"
STABLE_TARGET = "groq/openai/gpt-oss-20b"
DEAD_TARGET = "groq/llama-3.3-70b-versatile"


def test_release_metadata_is_exact() -> None:
    metadata = tomllib.loads((PLUGIN_DIR / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["version"] == "0.1.2"
    assert "freellmpool>=0.12.1" in metadata["project"]["dependencies"]
    assert metadata["project"]["urls"]["Repository"] == (
        "https://github.com/0xzr/freellmpool/tree/main/plugins/llm-freellmpool"
    )
    assert metadata["build-system"]["requires"] == ["hatchling==1.32.0"]


def test_examples_use_a_current_stable_groq_target() -> None:
    plugin = (PLUGIN_DIR / "llm_freellmpool.py").read_text(encoding="utf-8")
    readme = (PLUGIN_DIR / "README.md").read_text(encoding="utf-8")

    assert STABLE_TARGET in plugin
    assert STABLE_TARGET in readme
    assert DEAD_TARGET not in plugin
    assert DEAD_TARGET not in readme


def test_keyless_claims_keep_the_availability_caveat() -> None:
    metadata = tomllib.loads((PLUGIN_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = (PLUGIN_DIR / "llm_freellmpool.py").read_text(encoding="utf-8")
    readme = (PLUGIN_DIR / "README.md").read_text(encoding="utf-8")

    assert "keyless start when available" in metadata["project"]["description"]
    assert "zero API keys when a freellmpool keyless provider is available" in plugin
    assert "zero API keys while an enabled keyless route is available" in " ".join(
        readme.split()
    )
    assert "No key is required while an enabled keyless route is available." in " ".join(
        readme.split()
    )
    for unconditional_claim in (
        "always works with zero API keys",
        "zero API keys are required",
        "No API key is required.",
    ):
        assert unconditional_claim not in f"{plugin}\n{readme}"


def test_ci_builds_and_fresh_installs_the_plugin_wheel() -> None:
    workflow = CI.read_text(encoding="utf-8")

    assert workflow.count("\n  llm-plugin:\n") == 1
    job = workflow.split("\n  llm-plugin:\n", maxsplit=1)[1]
    for required in (
        "permissions:\n      contents: read",
        "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
        'python-version: "3.12"',
        "build==1.5.0",
        "twine==7.0.0",
        "PYTHONPATH=src pytest -q plugins/llm-freellmpool/tests",
        "python -m build --outdir plugins/llm-freellmpool/dist plugins/llm-freellmpool",
        "python -m twine check plugins/llm-freellmpool/dist/*.whl plugins/llm-freellmpool/dist/*.tar.gz",
        'python -m venv "$RUNNER_TEMP/llm-plugin-smoke"',
        "--no-deps plugins/llm-freellmpool/dist/*.whl",
        '"llm==0.33"',
        "plugins/llm-freellmpool/tests/smoke_cli.py",
    ):
        assert required in job


def test_publish_workflow_is_manual_protected_exact_sha_and_verified() -> None:
    workflow = PUBLISH.read_text(encoding="utf-8")
    trigger = workflow.split("permissions:", maxsplit=1)[0]

    assert "workflow_dispatch:" in trigger
    assert "push:" not in trigger
    assert "pull_request:" not in trigger
    assert workflow.count("pypa/gh-action-pypi-publish@") == 1
    for required in (
        "version:",
        "commit:",
        "authentication:",
        "trusted-publishing",
        "api-token",
        "name: pypi",
        "contents: read",
        "id-token: write",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        "pypa/gh-action-pypi-publish@ed0c53931b1dc9bd32cbe73a98c7f6766f8a527e",
        'git merge-base --is-ancestor "$RELEASE_COMMIT" origin/main',
        "git cat-file -t",
        "Revalidate immutable root release in the protected publish job",
        'refs/tags/$root_tag^{commit}',
        ".immutable",
        'SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"',
        "export SOURCE_DATE_EPOCH",
        "python -m build --outdir dist .",
        "python -m twine check dist/*.whl dist/*.tar.gz",
        'python -m venv "$RUNNER_TEMP/llm-plugin-local"',
        "-m pip check",
        "secrets.PYPI_API_TOKEN",
        "scripts/verify_pypi_artifacts.py",
        "--mode subset",
        "--mode exact",
        "skip-existing: true",
        "steps.preflight.outputs.upload_needed == 'true'",
        '"llm-freellmpool==$RELEASE_VERSION"',
        "plugins/llm-freellmpool/tests/smoke_cli.py",
    ):
        assert required in workflow

    build_text, remainder = workflow.split("\n  publish:\n", maxsplit=1)
    publish_text, verify_text = remainder.split("\n  verify:\n", maxsplit=1)
    assert "refusing to overwrite" not in build_text
    assert publish_text.index("--mode subset") < publish_text.index(
        "Validate the selected authentication mode"
    )
    assert publish_text.index(
        "Revalidate immutable root release in the protected publish job"
    ) < publish_text.index("--mode subset")
    assert "--mode exact" in verify_text
    assert "Fresh-install and smoke the registry wheel" in verify_text
