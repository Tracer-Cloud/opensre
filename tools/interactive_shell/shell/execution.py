"""Structured shell command execution helpers for the interactive REPL."""

from __future__ import annotations

import codecs
import contextlib
import io
import os
import subprocess
import threading
from dataclasses import dataclass
from typing import IO

from config.constants.terminal_host import (
    BASH_EXPORTED_FUNCTION_ENV_PREFIX,
)
from core.tool import truncate_output_text
from infrastructure.process.windows_job import WindowsJobProcess, spawn_windows_job
from tools.interactive_shell.shell.output_capture import OutputStream, ShellOutputCapture
from tools.interactive_shell.subprocess import OwnedProcessTree, watch_subprocess_until_exit


@dataclass(frozen=True)
class ShellExecutionResult:
    """Normalized command execution output."""

    command: str
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    truncated: bool
    executed_with_shell: bool
    cancelled: bool = False
    combined_output: str = ""


def _truncate_output(text: str, *, max_bytes: int) -> tuple[str, bool]:
    preview = truncate_output_text(text, max_bytes)
    return preview, preview != text


def _shell_argv(command: str) -> str | list[str]:
    if os.name == "nt":
        windows_shell = _windows_command_shell()
        # /d suppresses registry AutoRun commands before the approved command;
        # /v:off prevents inherited delayed !VAR! expansion from changing it.
        # Keep the tool contract's platform-neutral ``pwd`` diagnostic working:
        # bare ``cd`` is cmd.exe's current-directory display form.
        shell_command = "cd" if command.strip().lower() == "pwd" else command
        # cmd.exe parses the raw text following /c itself. Passing a sequence
        # makes subprocess apply C-runtime escaping first, which leaves literal
        # backslashes around embedded quotes and breaks quoted paths/operators.
        return f'"{windows_shell}" /d /v:off /s /c "{shell_command}"'
    # Do not use the interactive $SHELL: its startup hooks can run before the
    # command that policy classified. /bin/sh -c is non-interactive and stable.
    return ["/bin/sh", "-c", command]


def _windows_command_shell() -> str:
    """Return cmd.exe from Windows' system directory, not inherited COMSPEC."""
    import ctypes

    buffer = ctypes.create_unicode_buffer(32_768)
    kernel32 = ctypes.__dict__["windll"].kernel32
    length = kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if length == 0 or length >= len(buffer):
        raise OSError("Unable to locate the Windows system directory")
    return os.path.join(buffer.value, "cmd.exe")


def _shell_environment() -> dict[str, str]:
    """Copy the environment without Bash functions that can replace commands."""
    return {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(BASH_EXPORTED_FUNCTION_ENV_PREFIX)
    }


def _drain_pipe(pipe: IO[str] | None, capture: ShellOutputCapture, stream: OutputStream) -> None:
    """Read *pipe* to EOF so a chatty child cannot deadlock on a full buffer."""
    if pipe is None:
        return
    try:
        # Decode raw pipe bytes incrementally so a UTF-8 character split across
        # reads stays intact. Streams without a binary buffer are already text.
        buffer = pipe.buffer if isinstance(pipe, io.TextIOWrapper) else None
        if isinstance(buffer, io.BufferedReader):
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while chunk := buffer.read1(8192):
                capture.append(stream, decoder.decode(chunk))
            capture.append(stream, decoder.decode(b"", final=True))
        else:
            while text := pipe.read(8192):
                capture.append(stream, text)
    except (OSError, ValueError):
        # Cancellation can close the pipe while this reader is draining it.
        pass
    finally:
        with contextlib.suppress(OSError, ValueError):
            pipe.close()


def _cancelled_result(
    *,
    command: str,
) -> ShellExecutionResult:
    return ShellExecutionResult(
        command=command,
        stdout="",
        stderr="",
        exit_code=None,
        timed_out=False,
        truncated=False,
        executed_with_shell=True,
        cancelled=True,
    )


def execute_shell_command(
    *,
    command: str,
    timeout_seconds: int,
    max_output_bytes: int,
    cancel_event: threading.Event | None = None,
) -> ShellExecutionResult:
    """Execute a command and return a structured result object.

    Polls ``cancel_event`` while the child runs so ESC can stop ``shell_run``
    (and reap ``start_new_session`` descendants such as ``uv run opensre``)
    instead of blocking on ``subprocess.run`` until timeout.
    """
    watch_cancel = cancel_event if cancel_event is not None else threading.Event()
    if watch_cancel.is_set():
        return _cancelled_result(command=command)

    exec_argv = _shell_argv(command)

    with contextlib.ExitStack() as process_scope:
        owned_tree: OwnedProcessTree | None = None
        proc: subprocess.Popen[str] | WindowsJobProcess
        if os.name == "nt":
            assert isinstance(exec_argv, str)
            proc = process_scope.enter_context(
                spawn_windows_job(exec_argv, environment=_shell_environment())
            )
            owned_tree = proc
        else:
            proc = subprocess.Popen(
                exec_argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
                env=_shell_environment(),
            )
        return _collect_shell_result(
            proc,
            command=command,
            watch_cancel=watch_cancel,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            owned_tree=owned_tree,
        )


def _collect_shell_result(
    proc: subprocess.Popen[str] | WindowsJobProcess,
    *,
    command: str,
    watch_cancel: threading.Event,
    timeout_seconds: int,
    max_output_bytes: int,
    owned_tree: OwnedProcessTree | None,
) -> ShellExecutionResult:
    capture = ShellOutputCapture()
    readers = (
        threading.Thread(target=_drain_pipe, args=(proc.stdout, capture, "stdout"), daemon=True),
        threading.Thread(target=_drain_pipe, args=(proc.stderr, capture, "stderr"), daemon=True),
    )
    for reader in readers:
        reader.start()

    watch = watch_subprocess_until_exit(
        proc,
        cancel_event=watch_cancel,
        timeout_seconds=timeout_seconds,
        owned_tree=owned_tree,
        # start_new_session owns this group even if its leader has already exited.
        process_group_id=proc.pid if os.name != "nt" else None,
    )
    for reader in readers:
        reader.join(timeout=2.0)

    captured_stdout, captured_stderr, combined, capture_truncated = capture.snapshot()
    stdout, truncated_stdout = _truncate_output(
        captured_stdout,
        max_bytes=max_output_bytes,
    )
    stderr, truncated_stderr = _truncate_output(
        captured_stderr,
        max_bytes=max_output_bytes,
    )
    return ShellExecutionResult(
        command=command,
        stdout=stdout,
        stderr=stderr,
        exit_code=watch.exit_code,
        timed_out=watch.timed_out,
        truncated=capture_truncated or truncated_stdout or truncated_stderr,
        executed_with_shell=True,
        cancelled=watch.cancelled,
        combined_output=combined,
    )


__all__ = ["ShellExecutionResult", "execute_shell_command"]
