"""Role presets for the freellmpool CLI.

A role bundles a routing mode, output/token defaults, and an optional system
prompt prefix so users can say ``freellmpool ask --role coder ...`` instead of
manually tuning flags. Roles are intentionally lightweight and stdlib-only.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RoleSpec:
    """A named ask-role preset.

    Explicit user flags always beat role defaults; ``None`` here means "do not
    override the user's value or the pool default for this field".
    """

    name: str
    description: str
    routing: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    system_prefix: str | None = None
    task: str | None = None


_ROLE_SPECS: tuple[RoleSpec, ...] = (
    RoleSpec(
        name="coder",
        description="Quality routing, tuned for code generation and debugging.",
        routing="quality",
        max_tokens=2048,
        system_prefix=(
            "You are an expert programmer. Write clean, idiomatic, "
            "well-commented code and explain your reasoning when helpful."
        ),
    ),
    RoleSpec(
        name="critic",
        description="Low-temperature quality review; look for bugs and improvements.",
        routing="quality",
        temperature=0.1,
        system_prefix=(
            "You are a critical reviewer. Identify issues, risks, and ways to improve."
        ),
    ),
    RoleSpec(
        name="summarizer",
        description="Spread routing; produce concise summaries that preserve key facts.",
        routing="spread",
        max_tokens=768,
        system_prefix=(
            "Summarize the following text concisely, preserving key facts and trade-offs."
        ),
    ),
    RoleSpec(
        name="grounded-reader",
        description="Quality routing for faithful document reading and extraction.",
        routing="quality",
        max_tokens=1024,
        temperature=0.0,
        system_prefix=(
            "Read the supplied material faithfully. Distinguish stated facts from "
            "inference and do not invent details."
        ),
        task="grounded-reading",
    ),
    RoleSpec(
        name="long-context",
        description="Quality routing with larger token budget for detailed answers.",
        routing="quality",
        max_tokens=4096,
        system_prefix=(
            "Use the full context provided. Prefer thorough, complete answers over terse ones."
        ),
    ),
    RoleSpec(
        name="cheap",
        description="Spread routing with a small token budget to minimize usage.",
        routing="spread",
        max_tokens=512,
        system_prefix="Be concise to minimize token usage.",
    ),
    RoleSpec(
        name="conserve",
        description="Wise-mode companion; spread routing with a small quota-conscious budget.",
        routing="spread",
        max_tokens=512,
        system_prefix="Be concise and avoid unnecessary calls or long outputs.",
    ),
    RoleSpec(
        name="fast",
        description="Fast routing with a small token budget for quick replies.",
        routing="fast",
        max_tokens=512,
        system_prefix="Respond quickly and concisely.",
    ),
    RoleSpec(
        name="second-opinion",
        description="Quality-routed panel of diverse models for comparison.",
        routing="quality",
        max_tokens=512,
        system_prefix=(
            "Give an independent answer. Be concise, concrete, and explicit about uncertainty."
        ),
    ),
)

ROLE_SPECS = _ROLE_SPECS


def valid_roles() -> tuple[str, ...]:
    """Return the names of all registered roles."""
    return tuple(role.name for role in ROLE_SPECS)


def get_role(name: str) -> RoleSpec | None:
    """Look up a role by name, returning ``None`` for unknown roles."""
    lowered = (name or "").lower()
    for role in ROLE_SPECS:
        if role.name == lowered:
            return role
    return None


def format_roles() -> str:
    """Return a human-readable list of available roles."""
    lines: list[str] = ["Available roles:"]
    for role in ROLE_SPECS:
        extras: list[str] = []
        if role.routing is not None:
            extras.append(f"routing={role.routing}")
        else:
            extras.append("routing=pool default")
        if role.max_tokens is not None:
            extras.append(f"max_tokens={role.max_tokens}")
        if role.temperature is not None:
            extras.append(f"temperature={role.temperature}")
        if role.task is not None:
            extras.append(f"task={role.task}")
        lines.append(f"  {role.name:<16} {role.description} ({', '.join(extras)})")
    return "\n".join(lines)
