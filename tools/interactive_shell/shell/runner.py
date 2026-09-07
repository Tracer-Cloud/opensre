"""Shell command runner: execute builtins and record results."""

from __future__ import annotations

import os
import shlex
import threading
from pathlib import Path
from typing import Any

import config.constants.platform as _platform
from infrastructure.terminal.theme import ERROR, GLYPH_ERROR
from tools.interactive_shell.shell import execution as shell_execution
from tools.interactive_shell.shell.display import format_shell_command_for_display
from tools.interactive_shell.shell.parsing import (
    argv_for_repl_builtin_detection,
    parse_shell_command,
)
from tools.interactive_shell.shell.policy import plan_shell_execution
from tools.interactive_shell.subprocess import (
    MAX_COMMAND_OUTPUT_CHARS,
    SHELL_COMMAND_TIMEOUT_SECONDS,
    SubprocessPresenter,
)


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
    argv: list[str] | None = None,
    quiet: bool = False,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    session = presenter.session
    parsed = parse_shell_command(command, is_windows=_platform.IS_WINDOWS)
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

    if not quiet:
        presenter.print_bold_command(display_command)

    argv_builtin = argv_for_repl_builtin_detection(parsed=parsed, is_windows=_platform.IS_WINDOWS)

    if argv_builtin is not None and argv_builtin[0].lower() == "cd":
        return run_cd_command(parsed.command, presenter, quiet=quiet)
    if argv_builtin is not None and argv_builtin[0].lower() == "pwd":
        return run_pwd_command(parsed.command, presenter, quiet=quiet)

    use_shell = parsed.use_shell
    if parsed.passthrough and not quiet:
        presenter.print("[dim]explicit shell passthrough enabled[/]")

    exec_argv = argv if argv is not None else parsed.argv

    response_text: str | None = None

    try:
        result = shell_execution.execute_shell_command(
            command=parsed.command,
            argv=exec_argv,
            use_shell=use_shell,
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
            executed_with_shell=use_shell,
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


def run_cd_command(
    command: str,
    presenter: SubprocessPresenter,
    *,
    quiet: bool = False,
) -> dict[str, Any]:
    session = presenter.session

    def _strip_outer_quotes(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1]
        return value

    try:
        tokens = shlex.split(command, posix=not _platform.IS_WINDOWS)
        if _platform.IS_WINDOWS and len(tokens) > 1:
            tokens = [tokens[0], *(_strip_outer_quotes(token) for token in tokens[1:])]
    except ValueError as exc:
        response_text = f"cd failed: {str(exc)}"

        if not quiet:
            presenter.print_error(f"cd failed: {exc}")
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(command=command, ok=False, response_text=response_text)

    if len(tokens) > 2:
        response_text = "cd failed: too many arguments"

        if not quiet:
            presenter.print("[error]cd failed:[/] too many arguments")
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(command=command, ok=False, response_text=response_text)

    target = Path(tokens[1]).expanduser() if len(tokens) == 2 else Path.home()
    try:
        os.chdir(target)
    except Exception as exc:
        presenter.report_exception(exc, context="surfaces.interactive_shell.shell_cd")

        response_text = f"cd failed: {str(exc)}"

        if not quiet:
            presenter.print_error(f"cd failed: {exc}")
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(command=command, ok=False, response_text=response_text)

    cwd = str(Path.cwd())
    if not quiet:
        presenter.print_plain(cwd)
    session.record("shell", command)
    return _shell_payload(command=command, ok=True, response_text=cwd)


def run_pwd_command(
    command: str,
    presenter: SubprocessPresenter,
    *,
    quiet: bool = False,
) -> dict[str, Any]:
    session = presenter.session
    try:
        tokens = shlex.split(command, posix=not _platform.IS_WINDOWS)
    except ValueError as exc:
        response_text = f"pwd failed: {str(exc)}"

        if not quiet:
            presenter.print_error(f"pwd failed: {exc}")
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(command=command, ok=False, response_text=response_text)

    if len(tokens) != 1:
        response_text = "pwd failed: too many arguments"

        if not quiet:
            presenter.print("[error]pwd failed:[/] too many arguments")
        session.record("shell", command, ok=False, response_text=response_text)
        return _shell_payload(command=command, ok=False, response_text=response_text)

    cwd = str(Path.cwd())
    if not quiet:
        presenter.print_plain(cwd)
    session.record("shell", command)
    return _shell_payload(command=command, ok=True, response_text=cwd, stdout=cwd)


__all__ = ["run_cd_command", "run_pwd_command", "run_shell_command"]
