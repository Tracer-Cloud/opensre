"""Shell execution tool."""

from __future__ import annotations

import threading
from typing import Any

from config.constants.tool_output import TOOL_OUTPUT_BYTES_PER_TOKEN
from core.agent_harness.tools import (
    ActionToolScope,
    capability_available_from_sources,
    execute_with_action_context,
)
from core.domain.types.tools import ToolSurface
from core.tool import (
    CALL_SIDE_EFFECT_LEVEL_KEY,
    RegisteredTool,
    SideEffectLevel,
    ToolExecutionResult,
    tool_output_byte_budget,
    truncate_output_text,
)
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
    max_output_tokens = args.get("max_output_tokens")
    refusal = git_refusal_during_merge(command) or pull_request_checkout_refusal(command)
    if refusal is not None:
        return {"ok": False, "command": command, "response_text": refusal}
    payload = run_shell_command(
        command,
        require_subprocess_presenter(ctx),
        quiet=quiet,
        cancel_event=_turn_cancel_event(ctx.console),
        max_output_tokens=max_output_tokens if isinstance(max_output_tokens, int) else None,
    )
    if shell_command_only_reads(command):
        payload = {**payload, CALL_SIDE_EFFECT_LEVEL_KEY: SideEffectLevel.READ_ONLY.value}
    return payload


def run_shell(
    *, command: str, context: Any, quiet: bool = False, max_output_tokens: int | None = None
) -> ToolExecutionResult:
    payload = execute_with_action_context(
        {"command": command, "quiet": quiet, "max_output_tokens": max_output_tokens},
        context,
        execute_shell_tool,
    )
    output = (
        payload.get("output")
        or "\n".join(str(payload.get(key) or "") for key in ("stdout", "stderr")).strip()
    )
    if not output:
        output = str(payload.get("response_text") or "")
    output_budget = tool_output_byte_budget()
    if max_output_tokens is not None:
        output_budget = min(output_budget, max(max_output_tokens, 0) * TOOL_OUTPUT_BYTES_PER_TOKEN)
    sections = [
        f"Process exited with code {payload.get('exit_code')}",
        f"Timed out: {bool(payload.get('timed_out'))}",
        f"Cancelled: {bool(payload.get('cancelled'))}",
        "Output:",
        truncate_output_text(str(output), output_budget),
    ]
    return ToolExecutionResult(
        content="\n".join(sections), details=payload, provider_output_bounded=True
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
            "max_output_tokens": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Maximum approximate output tokens; defaults to 10000 and is limited "
                    "by the configured tool output budget. Oversized output keeps its "
                    "beginning and end."
                ),
            },
        },
        required=("command",),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    requires_approval=True,
    approval_reason="Runs a command on this machine.",
    accepts_runtime_context=True,
    run=run_shell,
    is_available=lambda sources: capability_available_from_sources(sources, "shell_commands"),
)


__all__ = ["execute_shell_tool", "shell_run_tool"]
