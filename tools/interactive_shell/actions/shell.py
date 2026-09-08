"""Shell execution tool."""

from __future__ import annotations

import threading
from typing import Any

from core.agent_harness.tools import (
    ActionToolScope,
    capability_available_from_sources,
    execute_with_action_context,
)
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel
from core.tool_framework.utils import object_schema, string_property
from tools.interactive_shell.shell.runner import run_shell_command
from tools.interactive_shell.subprocess import require_subprocess_presenter


def _coerce_quiet(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _turn_cancel_event(console: Any) -> threading.Event | None:
    event = getattr(console, "cancel_event", None)
    return event if isinstance(event, threading.Event) else None


def execute_shell_tool(args: dict[str, Any], ctx: ActionToolScope) -> dict[str, Any]:
    command = str(args.get("command", "")).strip()
    if not command:
        return {"ok": False, "command": "", "response_text": "missing shell command"}
    quiet = _coerce_quiet(args.get("quiet", False))
    return run_shell_command(
        command,
        require_subprocess_presenter(ctx),
        quiet=quiet,
        cancel_event=_turn_cancel_event(ctx.console),
    )


def run_shell(*, command: str, context: Any, quiet: bool = False) -> dict[str, Any]:
    return execute_with_action_context(
        {"command": command, "quiet": quiet},
        context,
        execute_shell_tool,
    )


shell_run_tool = RegisteredTool(
    name="shell_run",
    description=(
        "Run a local shell command on this machine. Use for read-only inspection, "
        "controlled operational steps, and user-requested local workflows — including "
        "creating files or scripts and executing multi-step sequences, one shell_run call "
        "per step when a step consumes the previous step's output. When the user asks for "
        "a specific command, propose it exactly as requested — the approval gate confirms "
        "anything risky before it runs, so do not refuse a destructive command the user "
        "explicitly asked for. Do not volunteer destructive, credential-exfiltrating, or "
        "unrelated commands the user did not ask for. Set quiet=true to hide the $ line "
        "and stdout/stderr from the terminal while still returning output to the agent "
        "(required for architecture-audit probes)."
    ),
    input_schema=object_schema(
        properties={
            "command": string_property(
                description=(
                    "Exact shell command to execute — a diagnostic (for example: `ls`, "
                    "`pwd`, `git status`, `uv run python -m pytest ...`) or one step of a "
                    "local workflow the user asked for (writing a file or script, running "
                    "it, updating state a later step reads). Run a user-requested command "
                    "as written; the approval gate grades and confirms its risk. Do not "
                    "introduce commands that wipe data or alter unrelated system state on "
                    "your own initiative."
                ),
                min_length=1,
            ),
            "quiet": {
                "type": "boolean",
                "description": (
                    "When true, do not print the command line or stdout/stderr to the "
                    "interactive shell. Tool result payload is unchanged. Use for "
                    "intermediate skill fetches (morning-report weather/news curls, "
                    "architecture-audit scans) when the user should only see the "
                    "composed answer, not the raw $ output twice."
                ),
            },
        },
        required=("command",),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_shell,
    is_available=lambda sources: capability_available_from_sources(sources, "shell_commands"),
)


__all__ = ["execute_shell_tool", "shell_run_tool"]
