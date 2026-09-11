"""Shell command runner: execute builtins and record results."""

from __future__ import annotations

import threading
from typing import Any

from infrastructure.terminal.theme import DIM, ERROR, GLYPH_ERROR
from tools.interactive_shell.shell import execution as shell_execution
from tools.interactive_shell.shell.display import (
    format_shell_command_for_display,
    summarize_shell_command,
)
from tools.interactive_shell.shell.parsing import parse_shell_command
from tools.interactive_shell.shell.policy import plan_shell_execution
from tools.interactive_shell.subprocess import (
    MAX_COMMAND_OUTPUT_CHARS,
    SHELL_COMMAND_TIMEOUT_SECONDS,
    SubprocessPresenter,
)
from tools.interactive_shell.working_directory import session_working_directory


def _shell_payload(
    *,
    command: str,
    ok: bool,
    response_text: str | None = None,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    timed_out: bool = False,
    truncated: bool = False,
    executed_with_shell: bool | None = None,
    cancelled: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": ok,
        "command": command,
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "truncated": truncated,
        "cancelled": cancelled,
    }
    if executed_with_shell is not None:
        payload["executed_with_shell"] = executed_with_shell
    if response_text:
        payload["response_text"] = response_text.strip()
    return payload


def run_shell_command(
    command: str,
    presenter: SubprocessPresenter,
    *,
    quiet: bool = False,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    session = presenter.session
    parsed = parse_shell_command(command)
    plan = plan_shell_execution(parsed)
    display_command = format_shell_command_for_display(command)
    if not presenter.execution_allowed(
        plan.policy,
        action_summary=f"$ {display_command}",
    ):
        session.record("shell", command, ok=False)
        return _shell_payload(
            command=command,
            ok=False,
            response_text=plan.policy.reason or "shell command blocked",
            cancelled=plan.policy.verdict != "deny",
        )

    if quiet:
        # Quiet hides the output, not the fact that a command ran.
        presenter.print(f"[{DIM}]$ {summarize_shell_command(display_command)}[/]")
    else:
        presenter.print_bold_command(display_command)

    if parsed.passthrough and not quiet:
        presenter.print("[dim]explicit shell passthrough enabled[/]")

    response_text: str | None = None

    try:
        result = shell_execution.execute_shell_command(
            command=parsed.command,
            cwd=session_working_directory(session),
            timeout_seconds=SHELL_COMMAND_TIMEOUT_SECONDS,
            max_output_chars=MAX_COMMAND_OUTPUT_CHARS,
            cancel_event=cancel_event,
        )
    except Exception as exc:
        presenter.report_exception(exc, context="surfaces.interactive_shell.shell_command.start")

        response_text = f"command failed to start: {str(exc)}"

        if not quiet:
            presenter.print_error(f"command failed to start: {exc}")
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(
            command=command,
            ok=False,
            response_text=response_text,
            stderr=str(exc),
            executed_with_shell=True,
        )

    if not quiet:
        presenter.print_command_output(result.stdout)
        presenter.print_command_output(result.stderr, style=ERROR)
    if result.cancelled:
        response_text = "command cancelled"
        if not quiet:
            presenter.print(f"[{ERROR}]command cancelled[/]")
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(
            command=command,
            ok=False,
            response_text=response_text,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            truncated=result.truncated,
            executed_with_shell=result.executed_with_shell,
            cancelled=True,
        )
    if result.timed_out:
        response_text = f"command timed out after {SHELL_COMMAND_TIMEOUT_SECONDS} seconds"

        if not quiet:
            presenter.print(
                f"[{ERROR}]command timed out after {SHELL_COMMAND_TIMEOUT_SECONDS} seconds[/]"
            )
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(
            command=command,
            ok=False,
            response_text=response_text,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            timed_out=True,
            truncated=result.truncated,
            executed_with_shell=result.executed_with_shell,
        )
    ok = result.exit_code == 0
    had_stdout = bool((result.stdout or "").strip())
    had_stderr = bool((result.stderr or "").strip())
    if ok:
        if had_stdout:
            response_text = (result.stdout or "").strip()
        elif had_stderr:
            response_text = (result.stderr or "").strip()
    else:
        code = result.exit_code if result.exit_code is not None else "?"
        exit_text = f"{GLYPH_ERROR} exit {code}"
        if not quiet:
            presenter.print_error(exit_text)

        response_parts = []
        if had_stdout:
            response_parts.append((result.stdout or "").strip())
        if had_stderr:
            response_parts.append((result.stderr or "").strip())
        response_parts.append(exit_text)
        response_text = "\n".join(response_parts)

    session.record("shell", command, ok=ok, response_text=response_text)
    stderr_for_result = "" if ok and had_stdout else result.stderr
    return _shell_payload(
        command=command,
        ok=ok,
        response_text=response_text,
        stdout=result.stdout,
        stderr=stderr_for_result,
        exit_code=result.exit_code,
        timed_out=False,
        truncated=result.truncated,
        executed_with_shell=result.executed_with_shell,
    )


__all__ = ["run_shell_command"]
