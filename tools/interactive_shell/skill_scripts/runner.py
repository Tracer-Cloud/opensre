"""Run one authorized bundled helper without printing its command or subprocess output."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any

from core.agent_harness.tools import ActionToolScope, capability_not_explicitly_disabled
from tools.interactive_shell.subprocess import (
    MAX_COMMAND_OUTPUT_CHARS,
    SHELL_COMMAND_TIMEOUT_SECONDS,
    read_task_output,
    terminate_child_process,
    watch_subprocess_until_exit,
)


def _failed(scope: ActionToolScope, tool_name: str, error: str, **details: Any) -> dict[str, Any]:
    # The surface's print method handles plain text. Diagnostics stay in the payload.
    scope.console.print(f"{tool_name.replace('_', ' ')}: {error}")
    return {**details, "ok": False, "error": error}


def run_skill_script(
    *,
    skill_name: str,
    tool_name: str,
    script_path: str,
    arguments: dict[str, Any],
    scope: ActionToolScope,
) -> dict[str, Any]:
    """Execute only while the declaring skill and local shell capability remain active."""
    if (
        getattr(scope.session, "active_skill", None) != skill_name
        or not getattr(scope.session, "skill_discovery_enabled", True)
        or not capability_not_explicitly_disabled(scope.session, "shell_commands")
    ):
        return _failed(scope, tool_name, "This helper is unavailable outside its active skill.")
    cancel = getattr(scope.console, "cancel_event", None)
    if not isinstance(cancel, threading.Event):
        cancel = threading.Event()
    if cancel.is_set() or getattr(scope.console, "cancel_requested", False):
        return _failed(scope, tool_name, "Cancelled before execution.", cancelled=True)
    python = sys.executable
    if getattr(sys, "frozen", False):
        python = shutil.which("python3") or shutil.which("python") or ""
    if not python:
        return _failed(scope, tool_name, "Python is required to run this helper.")
    argv = [python, script_path, json.dumps(arguments)]
    try:
        with tempfile.SpooledTemporaryFile() as stdout, tempfile.SpooledTemporaryFile() as stderr:
            with subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            ) as process:
                try:
                    outcome = watch_subprocess_until_exit(
                        process,
                        cancel_event=cancel,
                        timeout_seconds=SHELL_COMMAND_TIMEOUT_SECONDS,
                    )
                finally:
                    if process.poll() is None:
                        terminate_child_process(process)
            output = read_task_output(stdout, limit=MAX_COMMAND_OUTPUT_CHARS)
            diagnostics = read_task_output(stderr, limit=MAX_COMMAND_OUTPUT_CHARS)
    except Exception as exc:
        return _failed(scope, tool_name, "The helper could not finish.", diagnostics=str(exc))
    if outcome.cancelled or outcome.timed_out:
        return _failed(
            scope,
            tool_name,
            "Cancelled; saved progress is retained."
            if outcome.cancelled
            else "Timed out; saved progress is retained.",
            cancelled=outcome.cancelled,
            timed_out=outcome.timed_out,
            diagnostics=diagnostics,
        )
    try:
        payload = json.loads(output)
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        return _failed(
            scope,
            tool_name,
            "The helper returned no valid result.",
            diagnostics=diagnostics,
            stdout=output,
            exit_code=outcome.exit_code,
        )
    if outcome.exit_code != 0 or not payload["ok"]:
        # Print fixed text, never raw child output or exception details.
        error = "The helper failed; details and saved progress are available."
        scope.console.print(f"{tool_name.replace('_', ' ')}: {error}")
        return {
            **payload,
            "ok": False,
            "error": payload.get("error") or "The helper exited unsuccessfully.",
            "exit_code": outcome.exit_code,
            "diagnostics": diagnostics or payload.get("diagnostics", ""),
        }
    return payload
