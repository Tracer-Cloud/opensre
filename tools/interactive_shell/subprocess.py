"""Pure subprocess primitives and presenter port for interactive-shell action tools."""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from core.agent_harness.tools import ActionToolScope
from tools.interactive_shell.shared import ExecutionPolicyResult

# --- constants ---

SHELL_COMMAND_TIMEOUT_SECONDS = 120
CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS = 1800
TASK_POLL_SECONDS = 0.25
MAX_COMMAND_OUTPUT_CHARS = 24_000
TASK_DIAG_CHARS = 2_000
SIGTERM_GRACE_SECONDS = 10
TASK_OUTPUT_JOIN_TIMEOUT_SECONDS = 2

# Width of the ``<task_id> <stream> │ `` prefix relayed subprocess lines add.
TASK_OUTPUT_PREFIX_WIDTH = 18
MIN_SUBPROCESS_TERMINAL_WIDTH = 60

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[mA-Za-z]")


# --- lifecycle ---


def _is_process_group_leader(pid: int) -> bool:
    """True when *pid* leads its group (typical after ``start_new_session``)."""
    if os.name == "nt" or not hasattr(os, "getpgid"):
        return False
    try:
        return os.getpgid(pid) == pid
    except OSError:
        return False


def _signal_child(proc: subprocess.Popen[Any], *, forceful: bool) -> None:
    """Signal a child, and its descendants when it leads a process group.

    Nested ``uv run opensre …`` / ``gh`` children survive a single-pid
    SIGTERM. Group leaders (``start_new_session=True``) are safe to
    ``killpg``; other children stay in the parent's group, so we must
    not signal that group.
    """
    if proc.poll() is not None:
        return
    pid = getattr(proc, "pid", None)
    if pid is not None and _is_process_group_leader(pid) and hasattr(os, "killpg"):
        sig = signal.SIGKILL if forceful else signal.SIGTERM
        with contextlib.suppress(OSError):
            os.killpg(pid, sig)
            return
    with contextlib.suppress(OSError):
        if forceful:
            proc.kill()
        else:
            proc.terminate()


def terminate_child_process(proc: subprocess.Popen[Any]) -> None:
    """Best-effort SIGTERM → wait → SIGKILL → wait without blocking forever."""
    if proc.poll() is not None:
        return
    _signal_child(proc, forceful=False)
    try:
        proc.wait(timeout=SIGTERM_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_child(proc, forceful=True)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)


def read_task_output(
    buf: tempfile.SpooledTemporaryFile[bytes] | None,  # type: ignore[type-arg]
    *,
    limit: int,
) -> str:
    """Read up to ``limit`` bytes from a captured output buffer, ANSI-stripped."""
    if buf is None:
        return ""
    try:
        buf.seek(0)
        raw = buf.read(limit).decode("utf-8", errors="replace").strip()
    except (OSError, ValueError):
        return ""
    return _ANSI_ESCAPE.sub("", raw)


def read_diag(buf: tempfile.SpooledTemporaryFile[bytes]) -> str:  # type: ignore[type-arg]
    """Read up to ``TASK_DIAG_CHARS`` bytes from a captured stderr buffer."""
    return read_task_output(buf, limit=TASK_DIAG_CHARS)


# --- environment ---


def subprocess_env_with_width(*, columns: int, lines: int | None = None) -> dict[str, str]:
    """Return ``os.environ`` patched so a piped Rich subprocess wraps to fit."""
    available = max(
        MIN_SUBPROCESS_TERMINAL_WIDTH,
        columns - TASK_OUTPUT_PREFIX_WIDTH - 1,
    )
    env = dict(os.environ)
    env["COLUMNS"] = str(available)
    env.setdefault("LINES", str(max(20, lines or 24)))
    return env


# --- watcher ---


@dataclass(frozen=True)
class SubprocessWatchResult:
    """Outcome of watching a child process until exit, timeout, or cancel."""

    timed_out: bool
    cancelled: bool
    exit_code: int | None
    terminated_by_watcher: bool


def watch_subprocess_until_exit(
    proc: subprocess.Popen[Any],
    *,
    cancel_event: threading.Event,
    timeout_seconds: float,
    poll_seconds: float = TASK_POLL_SECONDS,
) -> SubprocessWatchResult:
    """Poll ``proc`` until it exits, ``cancel_event`` is set, or ``timeout_seconds`` elapses."""
    started = time.monotonic()
    timed_out = False
    terminated_by_watcher = False
    while proc.poll() is None:
        if time.monotonic() - started > timeout_seconds:
            timed_out = True
            terminate_child_process(proc)
            terminated_by_watcher = True
            break
        if cancel_event.is_set():
            terminate_child_process(proc)
            terminated_by_watcher = True
            break
        time.sleep(poll_seconds)
    return SubprocessWatchResult(
        timed_out=timed_out,
        cancelled=cancel_event.is_set(),
        exit_code=proc.returncode,
        terminated_by_watcher=terminated_by_watcher,
    )


# --- presenter port ---


@runtime_checkable
class SubprocessPresenter(Protocol):
    """Surface-injected UI and session hooks for subprocess runners."""

    @property
    def session(self) -> Any:
        """Mutable per-turn session (``core.agent_harness.session.Session``)."""

    def execution_allowed(
        self,
        policy: ExecutionPolicyResult,
        *,
        action_summary: str,
    ) -> bool:
        """Apply execution policy UX and return whether the action may proceed."""

    def print(self, message: str = "") -> None:
        """Print a Rich-markup message."""

    def print_error(self, message: str) -> None:
        """Print an error-styled plain-text message."""

    def print_highlight(self, message: str) -> None:
        """Print a highlight-styled plain-text message."""

    def print_bold_command(self, display_command: str) -> None:
        """Print a ``$ <command>`` header line."""

    def print_command_output(self, text: str, *, style: str | None = None) -> None:
        """Print captured subprocess stdout/stderr."""

    def print_plain(self, text: str) -> None:
        """Print plain text without Rich markup interpretation."""

    def report_exception(self, exc: BaseException, *, context: str) -> None:
        """Report an unexpected exception to observability."""

    def subprocess_env(self) -> dict[str, str]:
        """Environment for child subprocesses with terminal width alignment."""

    def start_task_output_streams(
        self,
        *,
        task: Any,
        proc: subprocess.Popen[Any],
        stdout_capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
        stderr_capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
    ) -> list[threading.Thread]:
        """Start relay threads for a background task's stdout/stderr."""

    def join_task_output_streams(self, threads: list[threading.Thread]) -> None:
        """Wait briefly for relay threads to finish."""

    def start_background_cli_task(
        self,
        *,
        display_command: str,
        argv_list: list[str],
        timeout_seconds: int,
        kind: Any,
        use_pty: bool = False,
    ) -> Any:
        """Launch a background opensre CLI subprocess with streamed output."""


def require_subprocess_presenter(ctx: ActionToolScope) -> SubprocessPresenter:
    presenter = ctx.subprocess_presenter
    if not isinstance(presenter, SubprocessPresenter):
        raise RuntimeError("subprocess presenter is required for this action tool")
    return presenter


__all__ = [
    "CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS",
    "MAX_COMMAND_OUTPUT_CHARS",
    "MIN_SUBPROCESS_TERMINAL_WIDTH",
    "SHELL_COMMAND_TIMEOUT_SECONDS",
    "SIGTERM_GRACE_SECONDS",
    "TASK_DIAG_CHARS",
    "TASK_POLL_SECONDS",
    "SubprocessPresenter",
    "SubprocessWatchResult",
    "TASK_OUTPUT_JOIN_TIMEOUT_SECONDS",
    "TASK_OUTPUT_PREFIX_WIDTH",
    "read_diag",
    "read_task_output",
    "require_subprocess_presenter",
    "subprocess_env_with_width",
    "terminate_child_process",
    "watch_subprocess_until_exit",
]
