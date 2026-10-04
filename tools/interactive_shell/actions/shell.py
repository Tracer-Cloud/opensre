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
from core.tool import CALL_SIDE_EFFECT_LEVEL_KEY, RegisteredTool, SideEffectLevel
from core.tool_framework.utils import object_schema, string_property
from tools.interactive_shell.shell.effects import shell_command_only_reads
from tools.interactive_shell.shell.merge_guard import (
    git_refusal_during_merge,
    pull_request_checkout_refusal,
)
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
    refusal = git_refusal_during_merge(command) or pull_request_checkout_refusal(command)
    if refusal is not None:
        return {"ok": False, "command": command, "response_text": refusal}
    payload = run_shell_command(
        command,
        require_subprocess_presenter(ctx),
        quiet=quiet,
        cancel_event=_turn_cancel_event(ctx.console),
    )
    if shell_command_only_reads(command):
        payload = {**payload, CALL_SIDE_EFFECT_LEVEL_KEY: SideEffectLevel.READ_ONLY.value}
    return payload


def run_shell(*, command: str, context: Any, quiet: bool = False) -> dict[str, Any]:
    return execute_with_action_context(
        {"command": command, "quiet": quiet},
        context,
        execute_shell_tool,
    )


shell_run_tool = RegisteredTool(
    name="shell_run",
    description=(
        "Run a local shell command on this machine. Native Windows uses cmd.exe; "
        "macOS, Linux, and WSL use /bin/sh. For PowerShell, invoke "
        '`powershell -NoProfile -Command "..."` or `pwsh -NoProfile -Command "..."` '
        "explicitly; PowerShell syntax is not translated. Use for read-only inspection, "
        "controlled operational steps, and user-requested local workflows — including "
        "creating files or scripts and executing multi-step sequences, one shell_run call "
        "per step when a step consumes the previous step's output. Each call starts a fresh shell, "
        "so directory changes and other shell state do not persist across calls. "
        'Prefix commands that need another directory with `cd /d "path" && command` '
        'on native Windows (including drive changes), or `cd "path" && command` '
        "on macOS, Linux, and WSL. When the user asks for a specific command, "
        "propose it exactly as requested. Do not refuse a destructive command the user "
        "explicitly asked for. Do not volunteer destructive, credential-exfiltrating, or "
        "unrelated commands the user did not ask for. Set quiet=true to hide stdout/stderr "
        "from the terminal while still returning output to the agent (required for "
        "intermediate skill probes); a dim command line still shows what ran. When a "
        "command errors (missing module, non-zero exit), fix and rerun it rather than "
        "estimating the result another way; never present an approximation as the "
        "measured figure."
    ),
    input_schema=object_schema(
        properties={
            "command": string_property(
                description=(
                    "Exact command for cmd.exe on native Windows or /bin/sh on macOS, Linux, "
                    "and WSL. For PowerShell, explicitly invoke "
                    '`powershell -NoProfile -Command "..."` or `pwsh -NoProfile -Command "..."`. '
                    "Run a diagnostic (for example: `dir` on Windows, `ls` on POSIX, "
                    "`pwd`, `git status`, `uv run python -m pytest ...`) or one step of a "
                    "local workflow the user asked for (writing a file or script, running "
                    'it, updating state a later step reads). Chain `cd /d "path" && command` '
                    'on native Windows or `cd "path" && command` on POSIX when a command '
                    "must run from another directory. Run a user-requested command "
                    "as written. Do not "
                    "introduce commands that wipe data or alter unrelated system state on "
                    "your own initiative."
                ),
                min_length=1,
            ),
            "quiet": {
                "type": "boolean",
                "description": (
                    "When true, do not print stdout/stderr to the interactive shell; the "
                    "command line still prints dimmed. Tool result payload is unchanged. Use for "
                    "intermediate skill fetches (delivering-morning-briefings weather/news "
                    "curls, repository scans) when the user should only see the "
                    "composed answer, not the raw $ output twice."
                ),
            },
        },
        required=("command",),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    accepts_runtime_context=True,
    run=run_shell,
    is_available=lambda sources: capability_available_from_sources(sources, "shell_commands"),
)


__all__ = ["execute_shell_tool", "shell_run_tool"]
