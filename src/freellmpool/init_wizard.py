"""First-run detection and setup-plan rendering for ``freellmpool init``."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import configured_providers, effective_env, load_catalog, settings
from .profiles import get_profile, profile_names
from .tailnet import (
    STATE_USABLE,
    SetupTokenLabel,
    detect_tailnet,
    format_setup_hints,
    safe_base_url,
)

DEFAULT_INIT_PORT = 8080

_AGENT_BINARIES: dict[str, str] = {
    "aider": "aider",
    "claude": "claude",
    "cline": "cline",
    "codex": "codex",
    "continue": "continue",
    "cursor": "cursor",
    "metaswarm": "metaswarm",
    "opencode": "opencode",
}


@dataclass(frozen=True)
class ProviderInitStatus:
    id: str
    label: str
    configured: bool
    keyless: bool
    key_env: str | None
    missing_env: tuple[str, ...]


@dataclass(frozen=True)
class AgentCliStatus:
    name: str
    binary: str
    installed: bool
    path: str | None


@dataclass(frozen=True)
class ProxyConfigStatus:
    config_file: str
    config_exists: bool
    provider_catalog: str
    provider_catalog_exists: bool
    proxy_key_configured: bool
    host: str
    port: int


@dataclass(frozen=True)
class InitReport:
    providers: tuple[ProviderInitStatus, ...]
    agent_clis: tuple[AgentCliStatus, ...]
    tailnet_state: str
    tailnet_usable: bool
    tailnet_ipv4: str | None
    tailnet_detail: str
    proxy_config: ProxyConfigStatus

    @property
    def configured_providers(self) -> tuple[ProviderInitStatus, ...]:
        return tuple(provider for provider in self.providers if provider.configured)

    @property
    def keyless_providers(self) -> tuple[ProviderInitStatus, ...]:
        return tuple(provider for provider in self.providers if provider.keyless)

    @property
    def missing_key_envs(self) -> tuple[str, ...]:
        names: set[str] = set()
        for provider in self.providers:
            if not provider.configured:
                names.update(provider.missing_env)
        return tuple(sorted(names))


def detect_environment(env: dict[str, str] | None = None) -> InitReport:
    env = dict(os.environ) if env is None else dict(env)
    provider_env = effective_env(env)
    catalog = load_catalog()
    configured_ids = {provider.id for provider in configured_providers(catalog, provider_env)}
    provider_statuses = tuple(
        ProviderInitStatus(
            id=provider.id,
            label=provider.label,
            configured=provider.id in configured_ids,
            keyless=bool(provider.keyless),
            key_env=provider.key_env,
            missing_env=_missing_env(provider, provider_env),
        )
        for provider in catalog
    )

    agent_statuses = tuple(
        _agent_status(name) for name in sorted(set(profile_names()) | set(_AGENT_BINARIES))
    )
    tailnet = detect_tailnet()
    cfg = settings(env)
    config_path = _config_path(env)
    provider_catalog = _provider_catalog_path(env)
    proxy = ProxyConfigStatus(
        config_file=str(config_path),
        config_exists=config_path.exists(),
        provider_catalog=str(provider_catalog),
        provider_catalog_exists=provider_catalog.exists(),
        proxy_key_configured=bool(env.get("FREELLMPOOL_PROXY_KEY") or cfg.get("proxy_key")),
        host=str(cfg.get("host") or "127.0.0.1"),
        port=_int_setting(cfg.get("port"), DEFAULT_INIT_PORT),
    )
    return InitReport(
        providers=provider_statuses,
        agent_clis=agent_statuses,
        tailnet_state=tailnet.state,
        tailnet_usable=tailnet.state == STATE_USABLE,
        tailnet_ipv4=tailnet.ipv4,
        tailnet_detail=tailnet.detail,
        proxy_config=proxy,
    )


def render_detect_only(report: InitReport, *, port: int = DEFAULT_INIT_PORT) -> str:
    configured = ", ".join(provider.id for provider in report.configured_providers) or "none"
    keyless = ", ".join(provider.id for provider in report.keyless_providers[:8]) or "none"
    missing_keys = ", ".join(report.missing_key_envs[:8]) or "none"
    installed_agents = ", ".join(agent.name for agent in report.agent_clis if agent.installed) or "none"
    proxy = report.proxy_config
    lines = [
        "freellmpool init: environment status",
        "",
        f"providers configured : {len(report.configured_providers)} ({configured})",
        f"keyless providers    : {len(report.keyless_providers)} ({keyless})",
        f"missing key env vars : {missing_keys}",
        f"agent CLIs found     : {installed_agents}",
        f"tailscale state      : {report.tailnet_state}",
        f"tailscale detail     : {report.tailnet_detail}",
        f"config file          : {proxy.config_file} ({_exists_label(proxy.config_exists)})",
        f"provider catalog     : {proxy.provider_catalog} ({_exists_label(proxy.provider_catalog_exists)})",
        f"proxy key configured : {'yes' if proxy.proxy_key_configured else 'no'}",
        "",
        "Recommended next commands:",
        "  freellmpool init --yes --agent opencode",
        f"  freellmpool init --yes --agent metaswarm --tailnet --port {port}",
        "  freellmpool mcp",
        "",
        "No files were written.",
    ]
    if report.missing_key_envs:
        lines.extend(
            [
                "",
                "To add provider keys, export the needed env vars or put them under",
                f"[keys] in {proxy.config_file}. Keyless providers can run without this step.",
            ]
        )
    if not report.tailnet_usable:
        lines.extend(["", f"Tailnet next step: {report.tailnet_detail}"])
    return "\n".join(lines)


def render_setup_plan(
    report: InitReport,
    *,
    agent: str | None = None,
    tailnet: bool = False,
    port: int = DEFAULT_INIT_PORT,
    force: bool = False,
) -> str:
    profile = get_profile(agent) if agent and agent != "mcp" else None
    if agent and agent != "mcp" and profile is None:
        valid = ", ".join(profile_names())
        raise ValueError(f"unknown agent '{agent}' (valid: {valid})")

    host = report.tailnet_ipv4 if tailnet and report.tailnet_ipv4 else "<tailnet-host>"
    base = safe_base_url(host, port) if tailnet else f"http://127.0.0.1:{port}"
    serve_cmd = (
        f"freellmpool tailnet serve --port {port}" if tailnet else f"freellmpool proxy --port {port}"
    )
    lines = [
        "freellmpool init: setup plan",
        "",
        f"target agent : {_target_label(agent)}",
        f"tailnet      : {'yes' if tailnet else 'no'}",
        f"write mode   : {'force requested, but no files are written by this setup plan' if force else 'print-only'}",
        "",
        "Copy-paste command block:",
        "```bash",
    ]
    if tailnet and not report.tailnet_usable:
        lines.extend(
            [
                "# Tailscale is not usable yet.",
                "# Fix this first, then run the serve command below:",
                f"# {report.tailnet_detail}",
            ]
        )
    lines.append(serve_cmd)
    if agent == "mcp":
        lines.append("freellmpool mcp")
    elif agent:
        lines.append(f"freellmpool profile install {agent}")
        doctor = f"freellmpool profile doctor {agent} --dry-run"
        if tailnet:
            doctor = f"{doctor} --base-url {base}"
        lines.append(doctor)
    if tailnet:
        lines.append(f"freellmpool tailnet connect {host} --port {port}")
        lines.append("")
        lines.append("# Remote client environment")
        lines.extend(
            _shell_safe_setup_lines(
                format_setup_hints(
                    base_url=base,
                    auth_enabled=True,
                    token_label=SetupTokenLabel.PROXY_KEY_FROM_SERVER,
                )
            )
        )
        lines.append("```")
    else:
        lines.append("```")
        lines.extend(
            [
                "",
                "Local client environment:",
                f"  export OPENAI_BASE_URL={base}/v1",
                "  export OPENAI_API_KEY=anything",
                f"  export FREELLMPOOL_BASE_URL={base}/v1",
            ]
        )
    lines.append("")
    lines.append("No files were written.")
    return "\n".join(lines)


def render_interactive_intro(report: InitReport) -> str:
    return "\n".join(
        [
            render_detect_only(report),
            "",
            "Interactive setup choices: local, proxy, mcp, tailnet, or any agent profile name.",
        ]
    )


def report_to_json(report: InitReport) -> str:
    return json.dumps(report_to_dict(report), indent=2, sort_keys=True)


def report_to_dict(report: InitReport) -> dict[str, Any]:
    return {
        "providers": [
            {
                "id": provider.id,
                "label": provider.label,
                "configured": provider.configured,
                "keyless": provider.keyless,
                "key_env": provider.key_env,
                "missing_env": list(provider.missing_env),
            }
            for provider in report.providers
        ],
        "agent_clis": {
            agent.name: {
                "binary": agent.binary,
                "installed": agent.installed,
                "path": agent.path,
            }
            for agent in report.agent_clis
        },
        "tailnet": {
            "state": report.tailnet_state,
            "usable": report.tailnet_usable,
            "ipv4": report.tailnet_ipv4,
            "detail": report.tailnet_detail,
        },
        "proxy_config": {
            "config_file": report.proxy_config.config_file,
            "config_exists": report.proxy_config.config_exists,
            "provider_catalog": report.proxy_config.provider_catalog,
            "provider_catalog_exists": report.proxy_config.provider_catalog_exists,
            "proxy_key_configured": report.proxy_config.proxy_key_configured,
            "host": report.proxy_config.host,
            "port": report.proxy_config.port,
        },
    }


def interactive_choice_to_plan(choice: str) -> tuple[str | None, bool] | None:
    normalized = choice.strip().lower()
    if normalized in {"", "detect", "status"}:
        return None
    if normalized in {"local", "proxy"}:
        return (None, False)
    if normalized == "mcp":
        return ("mcp", False)
    if normalized == "tailnet":
        return (None, True)
    if get_profile(normalized) is not None:
        return (normalized, False)
    return None


def _agent_status(name: str) -> AgentCliStatus:
    binary = _AGENT_BINARIES.get(name, name)
    path = shutil.which(binary)
    return AgentCliStatus(name=name, binary=binary, installed=bool(path), path=path)


def _target_label(agent: str | None) -> str:
    if agent == "mcp":
        return "MCP server"
    return agent or "local CLI"


def _shell_safe_setup_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.rstrip().splitlines():
        line = raw.lstrip()
        if not line:
            lines.append("")
        elif line.startswith("export "):
            lines.append(line)
        else:
            lines.append(f"# {line}")
    return lines


def _missing_env(provider, env: dict[str, str]) -> tuple[str, ...]:
    missing = [name for name in provider.extra_env if not env.get(name)]
    if not provider.keyless and provider.key_env and not env.get(provider.key_env):
        missing.append(provider.key_env)
    return tuple(missing)


def _config_path(env: dict[str, str]) -> Path:
    override = env.get("FREELLMPOOL_CONFIG_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "freellmpool" / "config.toml"


def _provider_catalog_path(env: dict[str, str]) -> Path:
    override = env.get("FREELLMPOOL_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "freellmpool" / "providers.toml"


def _int_setting(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _exists_label(exists: bool) -> str:
    return "exists" if exists else "missing"
