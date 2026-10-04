"""MCP-style slash-command catalog for LLM planners and tool specs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.tool_framework.utils import object_schema, string_array_property, string_property
from surfaces.interactive_shell.command_registry.types import SlashCommand
from tools.interactive_shell.shared.slash_catalog import MCP_BY_COMMAND

_MAX_COMPACT_DESC_CHARS = 120


@dataclass(frozen=True)
class SlashCommandSpec:
    name: str
    description: str
    llm_description: str
    use_cases: tuple[str, ...]
    anti_examples: tuple[str, ...]
    usage: tuple[str, ...]
    examples: tuple[str, ...]
    args_schema: dict[str, Any] | None


@dataclass(frozen=True)
class _ResolvedMcpFields:
    llm_description: str
    use_cases: tuple[str, ...]
    anti_examples: tuple[str, ...] = ()


def _resolve_mcp_fields(command: SlashCommand) -> _ResolvedMcpFields:
    registry = MCP_BY_COMMAND.get(command.name)
    llm_description = (
        command.llm_description or (registry.llm_description if registry else "")
    ).strip()
    if not llm_description:
        llm_description = command.description.strip()
        if command.usage:
            llm_description = f"{llm_description} Common forms: {', '.join(command.usage[:3])}."

    use_cases = command.use_cases or (registry.use_cases if registry else ())
    if not use_cases:
        use_cases = (f"User intent matches: {command.description.rstrip('.')}",)

    anti_examples = command.anti_examples or (registry.anti_examples if registry else ())
    return _ResolvedMcpFields(
        llm_description=llm_description,
        use_cases=use_cases,
        anti_examples=anti_examples,
    )


def _derive_args_schema(command: SlashCommand) -> dict[str, Any] | None:
    if command.args_schema is not None:
        return command.args_schema
    if not command.first_arg_completions:
        return None
    hints = "; ".join(f"{keyword} ({label})" for keyword, label in command.first_arg_completions)
    return {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            f"Positional arguments after {command.name}. First-argument options: {hints}."
        ),
    }


def spec_from_command(command: SlashCommand) -> SlashCommandSpec:
    mcp = _resolve_mcp_fields(command)
    return SlashCommandSpec(
        name=command.name,
        description=command.description,
        llm_description=mcp.llm_description,
        use_cases=mcp.use_cases,
        anti_examples=mcp.anti_examples,
        usage=command.usage,
        examples=command.examples,
        args_schema=_derive_args_schema(command),
    )


def build_slash_command_specs(
    commands: dict[str, SlashCommand] | None = None,
) -> list[SlashCommandSpec]:
    from surfaces.interactive_shell.command_registry import SLASH_COMMANDS

    source = commands if commands is not None else SLASH_COMMANDS
    return [spec_from_command(source[name]) for name in sorted(source.keys())]


def format_slash_catalog_text(
    specs: list[SlashCommandSpec] | None = None,
    *,
    compact: bool = False,
) -> str:
    entries = specs if specs is not None else build_slash_command_specs()
    if not entries:
        return ""

    lines: list[str] = []
    for spec in entries:
        desc = spec.llm_description
        if compact and len(desc) > _MAX_COMPACT_DESC_CHARS:
            desc = desc[: _MAX_COMPACT_DESC_CHARS - 1].rstrip() + "…"
        lines.append(f"- **{spec.name}** — {desc}")
        if spec.use_cases and not compact:
            lines.append(f"  - use when: {spec.use_cases[0]}")
        if spec.anti_examples and not compact:
            lines.append(f"  - not for: {spec.anti_examples[0]}")
        if spec.usage and not compact:
            lines.append(f"  - usage: {', '.join(spec.usage[:2])}")
    return "\n".join(lines)


def slash_invoke_tool_description(specs: list[SlashCommandSpec] | None = None) -> str:
    entries = specs if specs is not None else build_slash_command_specs()
    header = (
        "Run a slash command. "
        "Use this only for explicit slash-command operations: literal /command "
        "text, requests that explicitly ask to run a slash command, requests to configure "
        "or connect an integration, or "
        "operation/discovery cases that the system prompt explicitly maps to a "
        "slash command. Do not use this as a natural-language router for "
        "ordinary informational, how-to, capability, or status questions merely "
        "because a slash command can display related information; answer those "
        "directly unless a prompt rule names a read-only discovery exception. "
        "For requests such as Can you configure Telegram/GitHub/PostHog for me, "
        'call slash_invoke(command="/integrations", args=["setup", "telegram"/"github"/"posthog"]). '
        "The guided flow explains each human action, collects secrets privately, discovers IDs, "
        "and verifies the result. Do not ask for tokens in chat or use cli_exec for interactive setup. "
        "Supply positional args in the args array. This tool covers "
        "only the slash-command clause of a request. For compound requests, "
        "still emit a separate tool call for every other actionable clause in "
        "order; for example "
        "`run /remote and then check disk usage` requires "
        'slash_invoke(command="/remote", args=[]) followed by '
        'shell_run(command="df -h").'
    )
    # Keep planner payload intentionally tiny for live LLM runs with strict
    # prompt budgets. The full rich catalog remains available via
    # format_slash_catalog_text(..., compact=False).
    body = "\n".join(f"- `{spec.name}`" for spec in entries)
    return f"{header}\n\n{body}"


def slash_invoke_input_schema(
    specs: list[SlashCommandSpec] | None = None,
) -> dict[str, Any]:
    entries = specs if specs is not None else build_slash_command_specs()
    command_names = tuple(spec.name for spec in entries)
    args_description = (
        "Positional arguments after the command name. Valid values depend on the "
        "chosen command — see the slash_invoke tool description. Examples: "
        '["list"] for /tools, ["verify", "datadog"] for /integrations.'
    )
    return object_schema(
        properties={
            "command": string_property(
                description=(
                    "Slash command name only, including the leading `/` (e.g. "
                    "`/integrations`). Put every word after it in `args`, never in `command`."
                ),
                enum=command_names,
            ),
            "args": string_array_property(description=args_description),
        },
        required=("command",),
    )


__all__ = [
    "SlashCommandSpec",
    "build_slash_command_specs",
    "format_slash_catalog_text",
    "slash_invoke_input_schema",
    "slash_invoke_tool_description",
    "spec_from_command",
]
