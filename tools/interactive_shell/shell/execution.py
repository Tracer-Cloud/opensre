"""Structured shell command execution helpers for the interactive REPL."""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
from dataclasses import dataclass
from typing import IO

from tools.interactive_shell.subprocess import watch_subprocess_until_exit


@dataclass(frozen=True)
class ShellExecutionResult:
    """Normalized command execution output."""

    command: str
    argv: list[str] | None
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    truncated: bool
    executed_with_shell: bool
    cancelled: bool = False


def _truncate_output(text: str, *, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return f"{text[:max_chars].rstrip()}\n... output truncated ...", True


def _shell_argv(command: str) -> list[str]:
    if os.name == "nt":
        shell = os.environ.get("COMSPEC") or "cmd.exe"
        return [shell, "/d", "/s", "/c", command]
    shell = os.environ.get("SHELL") or "/bin/sh"
    return [shell, "-lc", command]


def _drain_pipe(pipe: IO[str] | None, buffer: list[str]) -> None:
    """Read *pipe* to EOF so a chatty child cannot deadlock on a full buffer."""
    if pipe is None:
        return
    try:
        for line in pipe:
            buffer.append(line)
    except (OSError, ValueError):
        pass
    finally:
        with contextlib.suppress(OSError, ValueError):
            pipe.close()


def _cancelled_result(
    *,
    command: str,
    argv: list[str] | None,
    use_shell: bool,
) -> ShellExecutionResult:
    return ShellExecutionResult(
        command=command,
        argv=argv,
        stdout="",
        stderr="",
        exit_code=None,
        timed_out=False,
        truncated=False,
        executed_with_shell=use_shell,
        cancelled=True,
    )


def execute_shell_command(
    *,
    command: str,
    argv: list[str] | None,
    use_shell: bool,
    timeout_seconds: int,
    max_output_chars: int,
    cancel_event: threading.Event | None = None,
) -> ShellExecutionResult:
    """Execute a command and return a structured result object.

    Polls ``cancel_event`` while the child runs so ESC can stop ``shell_run``
    (and reap ``start_new_session`` descendants such as ``uv run opensre``)
    instead of blocking on ``subprocess.run`` until timeout.
    """
    watch_cancel = cancel_event if cancel_event is not None else threading.Event()
    if watch_cancel.is_set():
        return _cancelled_result(command=command, argv=argv, use_shell=use_shell)

    if use_shell:
        exec_argv = _shell_argv(command)
    else:
        if argv is None:
            raise ValueError("argv is required for shell=False execution.")
        exec_argv = argv

    proc = subprocess.Popen(
        exec_argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    out_buf: list[str] = []
    err_buf: list[str] = []
    readers = (
        threading.Thread(target=_drain_pipe, args=(proc.stdout, out_buf), daemon=True),
        threading.Thread(target=_drain_pipe, args=(proc.stderr, err_buf), daemon=True),
    )
    for reader in readers:
        reader.start()

    watch = watch_subprocess_until_exit(
        proc,
        cancel_event=watch_cancel,
        timeout_seconds=timeout_seconds,
    )
    for reader in readers:
        reader.join(timeout=2.0)

    stdout, truncated_stdout = _truncate_output(
        "".join(out_buf),
        max_chars=max_output_chars,
    )
    stderr, truncated_stderr = _truncate_output(
        "".join(err_buf),
        max_chars=max_output_chars,
    )
    return ShellExecutionResult(
        command=command,
        argv=argv,
        stdout=stdout,
        stderr=stderr,
        exit_code=watch.exit_code,
        timed_out=watch.timed_out,
        truncated=truncated_stdout or truncated_stderr,
        executed_with_shell=use_shell,
        cancelled=watch.cancelled,
    )


__all__ = ["ShellExecutionResult", "execute_shell_command"]
