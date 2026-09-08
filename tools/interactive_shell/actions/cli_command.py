"""CLI command tool."""

from __future__ import annotations

from typing import Any

from core.agent_harness.tools import (
    ActionToolScope,
    capability_available_from_sources,
    execute_with_action_context,
)
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel
from core.tool_framework.utils import object_schema, string_property
from tools.interactive_shell.cli import run_opensre_cli_command
from tools.interactive_shell.subprocess import require_subprocess_presenter


def execute_cli_command_tool(args: dict[str, Any], ctx: ActionToolScope) -> bool:
    payload = str(args.get("payload", "")).strip()
    if not payload:
        return False
    run_opensre_cli_command(payload, require_subprocess_presenter(ctx))
    return True


def run_cli_command(*, payload: str, context: Any) -> dict[str, Any]:
    return execute_with_action_context({"payload": payload}, context, execute_cli_command_tool)


cli_exec_tool = RegisteredTool(
    name="cli_exec",
    description=(
        "Run an `opensre` CLI subcommand the user asked for (payload without the leading "
        "`opensre ` prefix), such as integrations/status/list/show/synthetic checks. "
        "Not a discovery tool: never run it to find a repository, a token, or configured "
        "integrations for a skill, and never run `health` unless the user asked for a "
        "health check; it prints every integration on the machine."
    ),
    input_schema=object_schema(
        properties={
            "payload": string_property(
                description=(
                    "CLI payload passed to `opensre` without the leading command prefix "
                    "(for example: `integrations list`, `synthetic run ...`). "
                    "Must not start with `opensre `."
                ),
                min_length=1,
            )
        },
        required=("payload",),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_cli_command,
    is_available=lambda sources: capability_available_from_sources(sources, "cli_commands"),
)


__all__ = ["cli_exec_tool", "execute_cli_command_tool"]
