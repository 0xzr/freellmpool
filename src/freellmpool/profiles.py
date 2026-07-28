"""Installable agent profiles for `freellmpool profile` and `freellmpool code`.

Each profile records how to wire a coding agent, an orchestration framework, or a
Metaswarm lane to freellmpool. Profiles are stdlib-only dataclasses and stay
independent of provider SDKs.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

_CLIENT_KINDS = ("openai", "anthropic", "mcp", "shell")
_COST_CLASSES = ("free", "metered", "paid")

ClientKind = Literal["openai", "anthropic", "mcp", "shell"]
CostClass = Literal["free", "metered", "paid"]

_DEFAULT_PROXY = "http://localhost:8080"


@dataclass(frozen=True)
class DoctorCheck:
    """One sanity check a profile doctor can report (dry-run) or perform.

    Attributes:
        kind: ``binary`` (executable on PATH), ``env`` (env var set),
            or ``url`` (HTTP GET reachable).
        name: Human label for the check.
        target: Executable name, env var name, or full URL.
        path: Optional path appended to ``target`` when ``kind == "url"`` and
            ``target`` is a base URL. Ignored for other kinds.
        optional: If True, a failure is reported but does not make the doctor exit non-zero.
        method: HTTP method for URL checks. POST checks use an empty JSON body by
            default; 400 means the route exists and rejected an intentionally
            minimal request.
    """

    kind: Literal["binary", "env", "url"]
    name: str
    target: str
    path: str | None = None
    optional: bool = False
    method: Literal["GET", "POST"] = "GET"
    body: Mapping[str, object] | None = None

    def url(self) -> str:
        """Return the full URL this check would request."""
        if self.kind != "url":
            raise ValueError(f"{self.kind} check has no URL")
        if self.path:
            return f"{self.target.rstrip('/')}/{self.path.lstrip('/')}"
        return self.target


@dataclass(frozen=True)
class Profile:
    """A reusable wiring recipe for a coding agent or orchestration client.

    Attributes:
        name: Short profile id used on the CLI (e.g. ``opencode``).
        label: Human-readable name.
        client_kind: Whether the tool speaks OpenAI, Anthropic, MCP, or shell.
        base_url: The base URL the tool should point at.
            OpenAI profiles include ``/v1``; Anthropic profiles do not.
        model_family: Logical model family or routing keyword (e.g. ``auto``).
        cost_class: Who pays: ``free`` (freellmpool pool), ``metered``, or ``paid``.
        role_map: Mapping from ask-role / orchestration role to a one-line note
            explaining why the profile fits the role.
        config_snippets: Copy-pastable config blocks keyed by a short label.
        doctor_checks: Checks the doctor reports or runs for this profile.
        notes: Optional caveat / extra context.
    """

    name: str
    label: str
    client_kind: ClientKind
    base_url: str
    model_family: str
    cost_class: CostClass
    role_map: Mapping[str, str]
    config_snippets: Mapping[str, str]
    doctor_checks: Sequence[DoctorCheck]
    notes: str | None = None


_COST_PRECEDENCE: dict[CostClass, int] = {"free": 0, "metered": 1, "paid": 2}

_PROFILES: tuple[Profile, ...] = (
    Profile(
        name="opencode",
        label="opencode",
        client_kind="openai",
        base_url=f"{_DEFAULT_PROXY}/v1",
        model_family="agent",
        cost_class="free",
        role_map={
            "coder": "default free worker lane; uses pool routing and avoids 429 storms",
            "fast": "lowest-latency free model for quick iterations",
        },
        config_snippets={
            "opencode.json": json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "model": "freellmpool/agent",
                    "provider": {
                        "freellmpool": {
                            "npm": "@ai-sdk/openai-compatible",
                            "options": {
                                "baseURL": f"{_DEFAULT_PROXY}/v1",
                                "headerTimeout": 60_000,
                                "timeout": 600_000,
                                "chunkTimeout": 120_000,
                            },
                            "models": {
                                "agent": {},
                                "spread": {},
                                "auto": {},
                                "fast": {},
                                "quality": {},
                                "fair": {},
                            },
                        }
                    },
                },
                indent=2,
            ),
            "env exports": (
                f"export OPENAI_BASE_URL={_DEFAULT_PROXY}/v1\n"
                "export OPENAI_API_KEY=anything   # ignored by freellmpool"
            ),
        },
        doctor_checks=[
            DoctorCheck("binary", "opencode CLI", "opencode"),
            DoctorCheck("binary", "freellmpool CLI", "freellmpool"),
            DoctorCheck("url", "proxy /v1/models", _DEFAULT_PROXY, path="/v1/models"),
        ],
        notes=(
            "Use freellmpool/agent for long-running tool work: it stays on the "
            "strongest benchmark tier and spreads usage within that tier."
        ),
    ),
    Profile(
        name="hermes",
        label="Hermes Agent",
        client_kind="openai",
        base_url=f"{_DEFAULT_PROXY}/v1",
        model_family="quality",
        cost_class="free",
        role_map={
            "coder": "agentic custom-endpoint lane using capability-aware free routing",
        },
        config_snippets={
            "~/.hermes/config.yaml": (
                "model:\n"
                "  provider: custom\n"
                "  default: quality\n"
                f"  base_url: {_DEFAULT_PROXY}/v1\n"
                "  api_key: anything"
            ),
        },
        doctor_checks=[
            DoctorCheck("binary", "hermes CLI", "hermes"),
            DoctorCheck("binary", "freellmpool CLI", "freellmpool"),
            DoctorCheck("url", "proxy /v1/models", _DEFAULT_PROXY, path="/v1/models"),
        ],
        notes=(
            "Hermes supports this as a custom OpenAI-compatible endpoint. "
            "Run 'hermes model' for the interactive setup instead."
        ),
    ),
    Profile(
        name="codex",
        label="OpenAI Codex CLI",
        client_kind="openai",
        base_url=f"{_DEFAULT_PROXY}/v1",
        model_family="auto",
        cost_class="paid",
        role_map={
            "escalation": "user-owned paid tool; route through freellmpool only when explicitly pointed",
        },
        config_snippets={
            "shell": (
                f"export OPENAI_BASE_URL={_DEFAULT_PROXY}/v1\n"
                "export OPENAI_API_KEY=anything\n"
                "codex --config model_provider=openai"
            ),
            "~/.codex/config.toml": (
                '[model]\nprovider = "openai"\n\n'
                f'[api]\nbase_url = "{_DEFAULT_PROXY}/v1"\n'
            ),
        },
        doctor_checks=[
            DoctorCheck("binary", "codex CLI", "codex"),
            DoctorCheck("binary", "freellmpool CLI", "freellmpool"),
            DoctorCheck("url", "proxy /v1/models", _DEFAULT_PROXY, path="/v1/models"),
            DoctorCheck(
                "url",
                "proxy /v1/responses",
                _DEFAULT_PROXY,
                path="/v1/responses",
                method="POST",
                body={},
            ),
        ],
        notes=(
            "Codex is a user-owned paid tool. The profile wires it through freellmpool "
            "only when explicitly selected; it is never chosen silently."
        ),
    ),
    Profile(
        name="cline",
        label="Cline / Roo Code (VS Code)",
        client_kind="openai",
        base_url=f"{_DEFAULT_PROXY}/v1",
        model_family="auto",
        cost_class="free",
        role_map={
            "coder": "OpenAI-compatible VS Code extension; point at the free pool",
        },
        config_snippets={
            "VS Code settings": (
                "Settings → API Provider: 'OpenAI Compatible'\n"
                f"  Base URL: {_DEFAULT_PROXY}/v1\n"
                "  API Key: anything\n"
                "  Model: auto"
            ),
        },
        doctor_checks=[
            DoctorCheck("binary", "VS Code", "code"),
            DoctorCheck("binary", "freellmpool CLI", "freellmpool"),
            DoctorCheck("url", "proxy /v1/models", _DEFAULT_PROXY, path="/v1/models"),
        ],
    ),
    Profile(
        name="cursor",
        label="Cursor / Windsurf",
        client_kind="openai",
        base_url=f"{_DEFAULT_PROXY}/v1",
        model_family="auto",
        cost_class="paid",
        role_map={
            "escalation": "paid editor frontier access; explicit model selection only",
        },
        config_snippets={
            "settings": (
                "Settings → Models → enable 'Override OpenAI Base URL':\n"
                f"  {_DEFAULT_PROXY}/v1   API key: anything   "
                "(free-tier models are slower than paid)"
            ),
        },
        doctor_checks=[
            DoctorCheck("binary", "Cursor", "cursor"),
            DoctorCheck("binary", "freellmpool CLI", "freellmpool"),
            DoctorCheck("url", "proxy /v1/models", _DEFAULT_PROXY, path="/v1/models"),
        ],
        notes="Cursor itself is a paid product; use the OpenAI-compatible override only when you explicitly choose it.",
    ),
    Profile(
        name="continue",
        label="Continue (VS Code / JetBrains)",
        client_kind="openai",
        base_url=f"{_DEFAULT_PROXY}/v1",
        model_family="auto",
        cost_class="free",
        role_map={
            "cheap": "lightweight continuation tasks on pooled free models",
        },
        config_snippets={
            "~/.continue/config.yaml": (
                "models:\n"
                "  - name: freellmpool\n"
                "    provider: openai\n"
                "    model: auto\n"
                f"    apiBase: {_DEFAULT_PROXY}/v1\n"
                "    apiKey: anything"
            ),
        },
        doctor_checks=[
            DoctorCheck("binary", "VS Code or JetBrains IDE", "code"),
            DoctorCheck("binary", "freellmpool CLI", "freellmpool"),
            DoctorCheck("url", "proxy /v1/models", _DEFAULT_PROXY, path="/v1/models"),
        ],
    ),
    Profile(
        name="claude",
        label="Claude Code",
        client_kind="anthropic",
        base_url=_DEFAULT_PROXY,
        model_family="auto",
        cost_class="paid",
        role_map={
            "final-review": "user-owned Opus/Claude final review; explicit model selection required",
        },
        config_snippets={
            "shell": (
                "export ANTHROPIC_BASE_URL=http://localhost:8080\n"
                "export ANTHROPIC_AUTH_TOKEN=dummy\n"
                "export ANTHROPIC_API_KEY=dummy\n"
                "export ANTHROPIC_MODEL=auto\n"
                "export ANTHROPIC_SMALL_FAST_MODEL=auto\n"
                "export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1\n"
                "claude"
            ),
        },
        doctor_checks=[
            DoctorCheck("binary", "claude CLI", "claude"),
            DoctorCheck("binary", "freellmpool CLI", "freellmpool"),
            DoctorCheck(
                "url",
                "proxy /v1/messages",
                _DEFAULT_PROXY,
                path="/v1/messages",
                method="POST",
                body={},
            ),
        ],
        notes="Pin a model with ANTHROPIC_MODEL=provider/model, e.g. alibaba_cloud_model_studio/qwen3-plus.",
    ),
    Profile(
        name="aider",
        label="aider",
        client_kind="openai",
        base_url=f"{_DEFAULT_PROXY}/v1",
        model_family="auto",
        cost_class="free",
        role_map={
            "coder": "terminal pair-programmer using pooled free models",
        },
        config_snippets={
            "shell": (
                "export OPENAI_API_BASE=http://localhost:8080/v1\n"
                "export OPENAI_API_KEY=anything\n"
                "aider --model openai/auto"
            ),
        },
        doctor_checks=[
            DoctorCheck("binary", "aider CLI", "aider"),
            DoctorCheck("binary", "freellmpool CLI", "freellmpool"),
            DoctorCheck("url", "proxy /v1/models", _DEFAULT_PROXY, path="/v1/models"),
        ],
    ),
    Profile(
        name="metaswarm",
        label="Metaswarm",
        client_kind="mcp",
        base_url=f"{_DEFAULT_PROXY}/v1",
        model_family="auto",
        cost_class="free",
        role_map={
            "worker": "free/cheap worker lane through the pooled proxy",
            "reviewer": "larger reviewer lane via freellmpool quality routing",
            "second-opinion": "adversarial review using a strong free model",
        },
        config_snippets={
            ".metaswarm/external-tools.yaml": (
                "adapters:\n"
                "  freellmpool-worker:\n"
                "    type: shell\n"
                "    role: implement\n"
                "    command: opencode\n"
                "    env:\n"
                f"      OPENAI_BASE_URL: {_DEFAULT_PROXY}/v1\n"
                "      OPENAI_API_KEY: anything\n"
                "  freellmpool-reviewer:\n"
                "    type: shell\n"
                "    role: review\n"
                "    command: freellmpool\n"
                "    args: [ask, --role, critic, --routing, quality]\n"
                "  codex-escalation:\n"
                "    type: shell\n"
                "    role: escalation\n"
                "    command: codex\n"
                "    # user-owned paid tool; configure your own OpenAI key\n"
                "  opus-final-review:\n"
                "    type: shell\n"
                "    role: final-review\n"
                "    command: claude\n"
                "    # user-owned paid tool; configure your own Anthropic key"
            ),
            "Tailnet remote agent": (
                "# On the agent machine, point at the Tailnet proxy:\n"
                "# (run `freellmpool tailnet connect <host> --port 8080` on the client)\n"
                "export OPENAI_BASE_URL=http://<tailnet-host>:8080/v1\n"
                "export OPENAI_API_KEY=<proxy-key-from-server>"
            ),
        },
        doctor_checks=[
            DoctorCheck("binary", "freellmpool CLI", "freellmpool"),
            DoctorCheck("binary", "opencode CLI (worker lane)", "opencode", optional=True),
            DoctorCheck("binary", "tailscale CLI (Tailnet)", "tailscale", optional=True),
            DoctorCheck("url", "proxy /v1/models", _DEFAULT_PROXY, path="/v1/models"),
        ],
        notes=(
            "Codex and Opus are documented as user-owned paid escalation lanes. "
            "They are never silently routed through freellmpool."
        ),
    ),
)

PROFILES: dict[str, Profile] = {p.name: p for p in _PROFILES}


def _profile_by_role_sort_key(profile: Profile) -> tuple[int, str]:
    return (_COST_PRECEDENCE.get(profile.cost_class, 99), profile.name)


def profile_names() -> tuple[str, ...]:
    """Return all registered profile names."""
    return tuple(PROFILES.keys())


def get_profile(name: str) -> Profile | None:
    """Look up a profile by id, returning ``None`` if it is unknown."""
    return PROFILES.get(name.lower())


def compatible_profiles(
    role: str, *, profiles: Iterable[Profile] | None = None
) -> tuple[Profile, ...]:
    """Return all profiles that advertise a given role, cheapest first.

    ``cost_class`` ordering is ``free < metered < paid`` so roles never silently
    upgrade to a more expensive profile.
    """
    if profiles is None:
        profiles = _PROFILES
    role = role.lower()
    matches = [p for p in profiles if role in {r.lower() for r in p.role_map}]
    return tuple(sorted(matches, key=_profile_by_role_sort_key))


def resolve_profile_for_role(
    role: str,
    *,
    explicit_model: str | None = None,
    explicit_profile: str | None = None,
    profiles: Iterable[Profile] | None = None,
) -> Profile | None:
    """Pick the safest (cheapest) profile for a role.

    Rules:

    * If the user passed an explicit model or profile name, use that profile
      when it exists.  This is the escape hatch.
    * Otherwise choose the cheapest compatible profile.
    * Paid profiles are never chosen silently; if the only matches are paid,
      return ``None`` so the caller can ask the user to pick explicitly.
    """
    if explicit_profile:
        return get_profile(explicit_profile)
    if explicit_model:
        return get_profile(explicit_model.split("/")[0])

    candidates = compatible_profiles(role, profiles=profiles)
    if not candidates:
        return None
    if candidates[0].cost_class == "paid":
        return None
    return candidates[0]


def render_profile(profile: Profile) -> str:
    """Render a profile for ``freellmpool profile show <name>``."""
    lines = [
        f"Profile: {profile.name}",
        f"  label:        {profile.label}",
        f"  client_kind:  {profile.client_kind}",
        f"  base_url:       {profile.base_url}",
        f"  model_family:   {profile.model_family}",
        f"  cost_class:     {profile.cost_class}",
        "",
        "  role map:",
    ]
    for role, note in sorted(profile.role_map.items()):
        lines.append(f"    {role:<16} {note}")
    lines.append("")
    lines.append("  config snippets:")
    keys = sorted(profile.config_snippets.keys())
    for key in keys:
        lines.append(f"    --- {key} ---")
        for ln in profile.config_snippets[key].splitlines():
            lines.append(f"    {ln}")
        lines.append("")
    lines.append("  doctor checks:")
    for check in profile.doctor_checks:
        suffix = " (optional)" if check.optional else ""
        target = check.url() if check.kind == "url" else check.target
        method = f" {check.method}" if check.kind == "url" else ""
        lines.append(f"    [{check.kind}{method}] {check.name}: {target}{suffix}")
    if profile.notes:
        lines.append("")
        lines.append(f"  note: {profile.notes}")
    return "\n".join(lines)


def render_profile_quickstart(profile: Profile) -> str:
    """Render the concise copy-paste block used by ``freellmpool code <agent>``."""
    lines = [f"Wire {profile.label} to free models via freellmpool:\n"]
    lines.append("  1. freellmpool proxy --port 8080")
    if "shell" in profile.config_snippets:
        lines.append("  2. Shell setup:")
        for ln in profile.config_snippets["shell"].splitlines():
            lines.append(f"    {ln}")
    else:
        # Fall back to the first snippet if no shell block exists.
        first_key = next(iter(profile.config_snippets))
        lines.append(f"  2. {first_key}:")
        for ln in profile.config_snippets[first_key].splitlines():
            lines.append(f"    {ln}")
        lines.append("")
    if profile.notes:
        lines.append(f"\n  ℹ {profile.notes}")
    lines.append("\n  More tools + details: docs/INTEGRATIONS.md")
    return "\n".join(lines)


def render_profile_list() -> str:
    """Render ``freellmpool profile list``."""
    lines = ["Available profiles:", ""]
    lines.append(f"  {'name':<12} {'kind':<10} {'cost':<8} {'label'}")
    for name in sorted(PROFILES):
        p = PROFILES[name]
        lines.append(f"  {name:<12} {p.client_kind:<10} {p.cost_class:<8} {p.label}")
    return "\n".join(lines)


def _root_base_url(base_url: str) -> str:
    """Return the proxy root URL even if an OpenAI `/v1` URL was supplied."""
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized[:-3]
    return normalized


def profile_with_base_url(profile: Profile, base_url: str) -> Profile:
    """Return ``profile`` with its proxy base URL and URL checks overridden."""
    root = _root_base_url(base_url)
    if profile.client_kind in {"openai", "mcp"}:
        profile_base = f"{root}/v1"
    else:
        profile_base = root
    checks = tuple(
        replace(check, target=root) if check.kind == "url" else check
        for check in profile.doctor_checks
    )
    return replace(profile, base_url=profile_base, doctor_checks=checks)


def render_doctor_plan(profile: Profile) -> str:
    """Render the checks ``doctor --dry-run`` would perform."""
    lines = [f"doctor plan for '{profile.name}' (dry-run; no network calls):"]
    for check in profile.doctor_checks:
        suffix = " optional" if check.optional else ""
        target = check.url() if check.kind == "url" else check.target
        method = f" {check.method}" if check.kind == "url" else ""
        lines.append(f"  [{check.kind}{method}{suffix}] {check.name}: {target}")
    return "\n".join(lines)


def run_doctor_check(
    check: DoctorCheck,
    *,
    timeout: float = 2.0,
    opener=urllib.request.urlopen,
) -> tuple[bool, str]:
    """Execute a single doctor check and return ``(ok, message)``.

    Stdlib network calls are used with a short timeout. No provider secrets are
    read or printed.
    """
    if check.kind == "binary":
        path = shutil.which(check.target)
        ok = path is not None
        msg = f"found at {path}" if ok else "not on PATH"
        return ok, msg

    if check.kind == "env":
        value = os.environ.get(check.target)
        ok = value is not None and value != ""
        # Never echo the real value.
        msg = "set" if ok else "not set"
        return ok, msg

    if check.kind == "url":
        url = check.url()
        data = None
        headers = {"User-Agent": "freellmpool-doctor/1.0"}
        if check.method == "POST":
            data = json.dumps(check.body or {}).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url,
            data=data,
            method=check.method,
            headers=headers,
        )
        try:
            with opener(req, timeout=timeout) as resp:
                _ = resp.read()
            return True, f"reachable ({resp.status})"
        except urllib.error.HTTPError as exc:
            # 400 proves a POST-only proxy route exists and rejected our minimal
            # probe before any provider call; 401/403 proves the proxy is gated.
            return exc.code in {400, 401, 403}, f"responded HTTP {exc.code}"
        except OSError as exc:
            return False, f"unreachable ({type(exc).__name__}: {exc})"

    return False, f"unknown check kind: {check.kind}"


def run_doctor(
    profile: Profile,
    *,
    timeout: float = 2.0,
    opener=urllib.request.urlopen,
) -> tuple[int, list[str]]:
    """Run every doctor check for a profile and return ``(exit_code, lines)``.

    Optional checks do not affect the exit code. Messages never include secrets.
    """
    lines = [f"doctor results for '{profile.name}':"]
    failed = 0
    for check in profile.doctor_checks:
        ok, msg = run_doctor_check(check, timeout=timeout, opener=opener)
        status = "ok" if ok else "FAIL"
        target = check.url() if check.kind == "url" else check.target
        line = f"  [{status}] {check.name} ({target}): {msg}"
        lines.append(line)
        if not ok and not check.optional:
            failed += 1
    if failed:
        lines.append(f"\n{failed} required check(s) failed.")
        return 1, lines
    lines.append("\nAll required checks passed.")
    return 0, lines


__all__ = [
    "ClientKind",
    "CostClass",
    "DoctorCheck",
    "Profile",
    "PROFILES",
    "compatible_profiles",
    "get_profile",
    "profile_names",
    "profile_with_base_url",
    "render_doctor_plan",
    "render_profile",
    "render_profile_list",
    "render_profile_quickstart",
    "resolve_profile_for_role",
    "run_doctor",
    "run_doctor_check",
]
