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
from tools.interactive_shell.shared import allow_tool
from tools.interactive_shell.shell.runner import run_shell_command
from tools.interactive_shell.subprocess import require_subprocess_presenter
from tools.interactive_shell.working_directory import (
    resolve_working_directory,
    session_working_directory,
)


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


def execute_set_working_directory_tool(
    args: dict[str, Any],
    ctx: ActionToolScope,
) -> dict[str, Any]:
    """Validate and commit the default directory for local session tools."""
    path = str(args.get("path", ""))
    if not path.strip():
        response_text = "missing working directory path"
        ctx.session.record("shell", "cd", ok=False, response_text=response_text)
        return {
            "ok": False,
            "working_directory": session_working_directory(ctx.session),
            "response_text": response_text,
        }

    command = f"cd {path}"
    try:
        resolved = resolve_working_directory(
            path,
            current=session_working_directory(ctx.session),
        )
    except (OSError, RuntimeError) as exc:
        response_text = f"working directory unchanged: {exc}"
        ctx.session.record("shell", command, ok=False, response_text=response_text)
        return {
            "ok": False,
            "working_directory": session_working_directory(ctx.session),
            "response_text": response_text,
        }

    presenter = require_subprocess_presenter(ctx)
    if not presenter.execution_allowed(
        allow_tool("shell"),
        action_summary=f"$ {command}",
    ):
        response_text = "working directory unchanged: action blocked"
        ctx.session.record("shell", command, ok=False, response_text=response_text)
        return {
            "ok": False,
            "working_directory": session_working_directory(ctx.session),
            "response_text": response_text,
        }

    ctx.session.set_working_directory(resolved)
    ctx.session.record("shell", command, response_text=resolved)
    return {
        "ok": True,
        "working_directory": resolved,
        "response_text": resolved,
    }


def set_working_directory(*, path: str, context: Any) -> dict[str, Any]:
    return execute_with_action_context(
        {"path": path},
        context,
        execute_set_working_directory_tool,
    )


shell_run_tool = RegisteredTool(
    name="shell_run",
    description=(
        "Run a local host-shell command on this machine. The command is evaluated as "
        "shell syntax, including quoting, expansions, redirects, and command operators. "
        "It starts in the session working directory. A `cd` inside this command affects "
        "only this child shell; use `set_working_directory` when later local tools should "
        "run from another directory. "
        "Use for read-only inspection, "
        "controlled operational steps, and user-requested local workflows — including "
        "creating files or scripts and executing multi-step sequences, one shell_run call "
        "per step when a step consumes the previous step's output. When the user asks for "
        "a specific command, propose it exactly as requested — the approval gate confirms "
        "anything risky before it runs, so do not refuse a destructive command the user "
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
                    "Exact host-shell command to execute — a diagnostic (for example: `ls`, "
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
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_shell,
    is_available=lambda sources: capability_available_from_sources(sources, "shell_commands"),
)


set_working_directory_tool = RegisteredTool(
    name="set_working_directory",
    description=(
        "Set the default working directory for subsequent local tools in this session, "
        "including shell_run, cli_exec, and code_implement. Use this for a standalone "
        "directory-change request instead of interpreting `cd` inside shell source. The "
        "path is structured data: pass it without shell quoting or environment-variable "
        "syntax. Relative paths resolve from the current session directory and `~` is "
        "supported. Invalid paths leave the current directory unchanged."
    ),
    input_schema=object_schema(
        properties={
            "path": string_property(
                description=(
                    "Directory to make current for later local tool calls. May be absolute, "
                    "relative to the current session directory, or start with `~`."
                ),
                min_length=1,
            )
        },
        required=("path",),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    use_cases=[
        "The user asks to change directories before later local tool calls.",
        "A multi-step workflow should continue from a different project directory.",
    ],
    anti_examples=[
        "A compound shell command already contains its own temporary `cd`; use shell_run.",
        "The user only asks which directory is active; use shell_run with `pwd`.",
    ],
    parallel_safe=False,
    accepts_runtime_context=True,
    run=set_working_directory,
    is_available=lambda sources: capability_available_from_sources(sources, "shell_commands"),
)


__all__ = [
    "execute_set_working_directory_tool",
    "execute_shell_tool",
    "set_working_directory_tool",
    "shell_run_tool",
]
