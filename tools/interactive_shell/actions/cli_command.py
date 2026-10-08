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
from tools.interactive_shell.cli import OpensreRunOutcome, run_opensre_cli_command_result
from tools.interactive_shell.subprocess import (
    MAX_COMMAND_OUTPUT_CHARS,
    require_subprocess_presenter,
)


def execute_cli_command_tool(args: dict[str, Any], ctx: ActionToolScope) -> dict[str, Any]:
    payload = str(args.get("payload", "")).strip()
    if not payload:
        return {"ok": False, "error": "A CLI payload is required."}
    result = run_opensre_cli_command_result(
        payload,
        require_subprocess_presenter(ctx),
        prefer_foreground=ctx.is_tty is False,
    )
    foreground = result.foreground
    if foreground is None:
        return {
            "ok": result.outcome
            in {
                OpensreRunOutcome.EXECUTED_FOREGROUND,
                OpensreRunOutcome.EXECUTED_BACKGROUND,
            },
            "outcome": result.outcome.value,
        }
    return {
        "ok": foreground.exit_code == 0
        and not foreground.timed_out
        and not foreground.start_failed,
        "outcome": result.outcome.value,
        "stdout": foreground.stdout[:MAX_COMMAND_OUTPUT_CHARS],
        "stderr": foreground.stderr[:MAX_COMMAND_OUTPUT_CHARS],
        "exit_code": foreground.exit_code,
        "timed_out": foreground.timed_out,
        "start_failed": foreground.start_failed,
    }


def run_cli_command(*, payload: str, context: Any) -> dict[str, Any]:
    return execute_with_action_context({"payload": payload}, context, execute_cli_command_tool)


cli_exec_tool = RegisteredTool(
    name="cli_exec",
    description=(
        "Run an `opensre` CLI subcommand the user asked for (payload without the leading "
        "`opensre ` prefix), such as integrations/status/list/show/synthetic checks. "
        "`payload` is the only argument (there is no `command` argument): when asked to "
        "run `opensre <args>` via cli_exec, pass `<args>` as `payload`. "
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
    accepts_runtime_context=True,
    run=run_cli_command,
    is_available=lambda sources: capability_available_from_sources(sources, "cli_commands"),
)


__all__ = ["cli_exec_tool", "execute_cli_command_tool"]
