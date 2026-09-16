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

from config.constants import (
    CAPABLE_TERMINAL_TYPE,
    DUMB_TERMINAL_TYPES,
    FORCE_COLOR_ENV,
    TERMINAL_COLUMNS_ENV,
    TERMINAL_LINES_ENV,
    TERMINAL_TYPE_ENV,
)
from core.agent_harness.tools import ActionToolScope
from infrastructure.process.termination import terminate_process_tree
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
# Render width for a child no human reads (gateway / headless turns). Only the
# action agent consumes that output, and it needs whole table cells — Rich at
# its 80-column non-TTY default ellipsizes ``/cron list`` ids to ``ecf7c2580b…``,
# which cannot be chained into ``/cron remove <id>``.
HEADLESS_SUBPROCESS_TERMINAL_WIDTH = 200

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


def _process_group_leader_pid(proc: subprocess.Popen[Any]) -> int | None:
    """Pgid to ``killpg`` when *proc* leads a session, else None.

    Snapshot this before the child exits: ``getpgid`` fails once the
    leader is reaped, and descendants can outlive it.
    """
    pid = proc.pid
    if not pid or not _is_process_group_leader(pid):
        return None
    return pid


def _process_group_is_alive(group_pid: int | None) -> bool:
    """Return whether a process group still has a member."""
    if group_pid is None or not hasattr(os, "killpg"):
        return False
    try:
        os.killpg(group_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _signal_child(
    proc: subprocess.Popen[Any],
    *,
    forceful: bool,
    group_pid: int | None,
) -> None:
    """Signal a child, and its descendants when it led a process group.

    Nested ``uv run opensre …`` / ``gh`` children survive a single-pid
    SIGTERM. Group leaders (``start_new_session=True``) are safe to
    ``killpg``; other children stay in the parent's group, so we must
    not signal that group. ``group_pid`` is snapshotted while the
    leader is still queryable so a later SIGKILL can reap leftovers
    after the leader has already exited.
    """
    if group_pid is not None and hasattr(os, "killpg"):
        sig = signal.SIGKILL if forceful else signal.SIGTERM
        with contextlib.suppress(OSError):
            os.killpg(group_pid, sig)
            return
    if proc.poll() is not None:
        return
    with contextlib.suppress(OSError):
        if forceful:
            proc.kill()
        else:
            proc.terminate()


def terminate_child_process(
    proc: subprocess.Popen[Any],
    *,
    group_pid: int | None = None,
) -> None:
    """Terminate the child and descendants, then forcefully reap leftovers.

    POSIX uses the process group created at launch. Windows has no equivalent
    group-wide primitive, so psutil snapshots the descendant tree before the
    shell parent can orphan it.
    """
    if os.name == "nt":
        pid = proc.pid
        # Check the Popen handle before resolving the PID through psutil. An
        # exited process may have had its PID reused by an unrelated process.
        if proc.poll() is None and isinstance(pid, int):
            terminate_process_tree(
                pid,
                grace_seconds=SIGTERM_GRACE_SECONDS,
                force_wait_seconds=5,
            )
        # Refresh Popen.returncode and retain a parent-only fallback when tree
        # inspection raced process exit or was denied.
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.kill()
        if proc.poll() is None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
        return

    if group_pid is None:
        group_pid = _process_group_leader_pid(proc)
    if proc.poll() is None:
        _signal_child(proc, forceful=False, group_pid=group_pid)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=SIGTERM_GRACE_SECONDS)
    _signal_child(proc, forceful=True, group_pid=group_pid)
    if proc.poll() is None:
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


def subprocess_env_with_width(
    *,
    columns: int,
    lines: int | None = None,
    prefix_width: int = TASK_OUTPUT_PREFIX_WIDTH,
) -> dict[str, str]:
    """Return ``os.environ`` patched so a piped Rich subprocess wraps to fit.

    ``prefix_width`` is what the parent prepends to every replayed line — the
    task-relay prefix by default, or the ``↳`` command-output gutter — so a
    child table row plus prefix still fits in ``columns`` without folding.
    """
    available = max(MIN_SUBPROCESS_TERMINAL_WIDTH, columns - prefix_width - 1)
    env = _piped_rich_env(columns=available)
    env.setdefault(TERMINAL_LINES_ENV, str(max(20, lines or 24)))
    return env


def headless_subprocess_env() -> dict[str, str]:
    """Return ``os.environ`` patched for a piped Rich child with no human reader."""
    return _piped_rich_env(columns=HEADLESS_SUBPROCESS_TERMINAL_WIDTH)


def _piped_rich_env(*, columns: int) -> dict[str, str]:
    """``os.environ`` with a width Rich will actually honour on a pipe.

    On a dumb ``TERM`` Rich short-circuits to 80x25 and ignores ``COLUMNS``,
    so the width hint is only meaningful once the terminal type is capable.
    """
    env = dict(os.environ)
    env[TERMINAL_COLUMNS_ENV] = str(columns)
    if env.get(TERMINAL_TYPE_ENV, "").lower() in DUMB_TERMINAL_TYPES:
        env[TERMINAL_TYPE_ENV] = CAPABLE_TERMINAL_TYPE
    return env


def force_rich_color(env: dict[str, str]) -> dict[str, str]:
    """Make a piped Rich child emit ANSI so a ``Text.from_ansi`` replay keeps its styling."""
    env[FORCE_COLOR_ENV] = "1"
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
    """Poll a child and its process group until exit, cancellation, or timeout."""
    started = time.monotonic()
    timed_out = False
    terminated_by_watcher = False
    group_pid = _process_group_leader_pid(proc)
    while proc.poll() is None or _process_group_is_alive(group_pid):
        if time.monotonic() - started > timeout_seconds:
            timed_out = True
            terminate_child_process(proc, group_pid=group_pid)
            terminated_by_watcher = True
            break
        if cancel_event.is_set():
            terminate_child_process(proc, group_pid=group_pid)
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
        """Environment for a piped child whose output this presenter replays.

        Sets ``COLUMNS`` so the child's Rich tables fit the reader: the real
        terminal minus the replay gutter on the REPL, or a wide fixed width on
        headless surfaces where only the action agent reads the output.
        """

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
    "HEADLESS_SUBPROCESS_TERMINAL_WIDTH",
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
    "force_rich_color",
    "headless_subprocess_env",
    "read_diag",
    "read_task_output",
    "require_subprocess_presenter",
    "subprocess_env_with_width",
    "terminate_child_process",
    "watch_subprocess_until_exit",
]
