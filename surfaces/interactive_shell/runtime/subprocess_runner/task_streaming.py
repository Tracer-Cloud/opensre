"""Shared subprocess-streaming primitives, PTY helpers, and module-wide constants."""

from __future__ import annotations

import contextlib
import errno
import os
import subprocess
import sys
import tempfile
import threading
from typing import IO, Any

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from infrastructure.scheduling.task_types import TaskRecord
from surfaces.interactive_shell.ui import DIM, ERROR
from surfaces.shared.error_handling.exception_reporting import report_exception
from tools.interactive_shell.subprocess import (
    CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS,
    MAX_COMMAND_OUTPUT_CHARS,
    MIN_SUBPROCESS_TERMINAL_WIDTH,
    SHELL_COMMAND_TIMEOUT_SECONDS,
    TASK_DIAG_CHARS,
    TASK_OUTPUT_JOIN_TIMEOUT_SECONDS,
    TASK_OUTPUT_PREFIX_WIDTH,
    TASK_POLL_SECONDS,
    read_diag,
    read_task_output,
    subprocess_env_with_width,
    terminate_child_process,
)

# Full dotted name of the ``subprocess_runner`` package. Submodules use this to
# look up patchable names from the parent namespace at call time so that tests
# using ``monkeypatch.setattr("…subprocess_runner.X", fake)`` take effect even
# when the implementation lives in a submodule.
_SUBPROCESS_RUNNER_MODULE = "surfaces.interactive_shell.runtime.subprocess_runner"

# Backward-compatible aliases for tests and callers using underscore-prefixed names.
_MAX_COMMAND_OUTPUT_CHARS = MAX_COMMAND_OUTPUT_CHARS
_TASK_POLL_SECONDS = TASK_POLL_SECONDS
_TASK_DIAG_CHARS = TASK_DIAG_CHARS
_MIN_SUBPROCESS_TERMINAL_WIDTH = MIN_SUBPROCESS_TERMINAL_WIDTH
_TASK_OUTPUT_PREFIX_WIDTH = TASK_OUTPUT_PREFIX_WIDTH
_TASK_OUTPUT_JOIN_TIMEOUT_SECONDS = TASK_OUTPUT_JOIN_TIMEOUT_SECONDS


def _sr_resolve(name: str, default: Any) -> Any:
    """Return ``subprocess_runner.<name>`` if the package is loaded, else ``default``.

    Used by submodules to honour monkeypatches applied to the parent package
    namespace (e.g. ``monkeypatch.setattr("…subprocess_runner.read_diag", …)``).
    """
    sr = sys.modules.get(_SUBPROCESS_RUNNER_MODULE)
    return getattr(sr, name, default) if sr is not None else default


def _print_task_output_line(
    console: Console,
    task: TaskRecord,
    stream_name: str,
    line: str,
    *,
    style: str | None = None,
) -> None:
    text = Text()
    text.append(f"{task.task_id} {stream_name} │ ", style=DIM)
    text.append(line.rstrip("\r\n"), style=style)
    console.print(text)


def _subprocess_env_with_aligned_width(console: Console) -> dict[str, str]:
    """Return ``os.environ`` patched so a piped Rich subprocess wraps to fit."""
    user_width = console.size.width or _MIN_SUBPROCESS_TERMINAL_WIDTH + _TASK_OUTPUT_PREFIX_WIDTH
    return subprocess_env_with_width(
        columns=user_width,
        lines=console.size.height,
    )


def _pump_task_stream(
    *,
    task: TaskRecord,
    stream_name: str,
    stream: IO[str],
    console: Console,
    style: str | None = None,
    capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
) -> None:
    try:
        for line in stream:
            if capture is not None:
                capture.write(line.encode("utf-8", errors="replace"))
            if line.strip():
                _print_task_output_line(console, task, stream_name, line, style=style)
                task.update_progress(line)
    except Exception as exc:  # noqa: BLE001
        report_exception(exc, context=f"surfaces.interactive_shell.task_stream.{stream_name}")
        console.print(f"[{DIM}]task output stream ended unexpectedly:[/] {escape(str(exc))}")


def _start_task_output_streams(
    *,
    task: TaskRecord,
    proc: subprocess.Popen[Any],
    console: Console,
    stdout_capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
    stderr_capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
) -> list[threading.Thread]:
    threads: list[threading.Thread] = []
    streams: tuple[tuple[str, IO[str] | None, str | None, Any], ...] = (
        ("stdout", proc.stdout, None, stdout_capture),
        ("stderr", proc.stderr, ERROR, stderr_capture),
    )
    for stream_name, stream, style, capture in streams:
        if stream is None:
            continue
        thread = threading.Thread(
            target=_pump_task_stream,
            kwargs={
                "task": task,
                "stream_name": stream_name,
                "stream": stream,
                "console": console,
                "style": style,
                "capture": capture,
            },
            daemon=True,
            name=f"task-output-{task.task_id}-{stream_name}",
        )
        thread.start()
        threads.append(thread)
    return threads


def _join_task_output_streams(threads: list[threading.Thread]) -> None:
    for thread in threads:
        thread.join(timeout=TASK_OUTPUT_JOIN_TIMEOUT_SECONDS)


def _console_file_is_tty(console: Console) -> bool:
    isatty = getattr(console.file, "isatty", None)
    return bool(isatty and isatty())


def _should_use_pty(console: Console, requested: bool) -> bool:
    return requested and hasattr(os, "openpty") and _console_file_is_tty(console)


def _pump_task_pty(
    *,
    master_fd: int,
    console: Console,
    capture: tempfile.SpooledTemporaryFile[bytes],  # type: ignore[type-arg]
) -> None:
    try:
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            capture.write(chunk)
            console.file.write(chunk.decode("utf-8", errors="replace"))
            console.file.flush()
    except Exception as exc:  # noqa: BLE001
        report_exception(exc, context="surfaces.interactive_shell.task_pty_stream")
        console.print(f"[{DIM}]task terminal stream ended unexpectedly:[/] {escape(str(exc))}")
    finally:
        with contextlib.suppress(OSError):
            os.close(master_fd)


__all__ = [
    "SHELL_COMMAND_TIMEOUT_SECONDS",
    "CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS",
    "_TASK_POLL_SECONDS",
    "_MAX_COMMAND_OUTPUT_CHARS",
    "_TASK_DIAG_CHARS",
    "_TASK_OUTPUT_PREFIX_WIDTH",
    "_MIN_SUBPROCESS_TERMINAL_WIDTH",
    "_TASK_OUTPUT_JOIN_TIMEOUT_SECONDS",
    "terminate_child_process",
    "read_diag",
    "read_task_output",
    "_print_task_output_line",
    "_subprocess_env_with_aligned_width",
    "_pump_task_stream",
    "_start_task_output_streams",
    "_join_task_output_streams",
    "_console_file_is_tty",
    "_should_use_pty",
    "_pump_task_pty",
]
