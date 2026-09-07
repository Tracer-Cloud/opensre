"""Tests for structured REPL shell execution."""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from tools.interactive_shell.shell.execution import (
    execute_shell_command,
)
from tools.interactive_shell.shell.parsing import parse_shell_command


def _assert_pid_gone(pid: int, *, timeout_seconds: float = 2.0) -> None:
    """Wait until *pid* is gone; a single ``kill(pid, 0)`` races a zombie."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.05)
    pytest.fail(f"process {pid} still alive after cancel")


def test_execute_shell_command_reports_timeout_argv_mode() -> None:
    started = time.monotonic()
    result = execute_shell_command(
        command="sleep 30",
        argv=["sleep", "30"],
        use_shell=False,
        timeout_seconds=1,
        max_output_chars=10_000,
    )
    assert result.timed_out is True
    assert result.cancelled is False
    assert time.monotonic() - started < 5


def test_execute_shell_command_reports_timeout_shell_mode() -> None:
    started = time.monotonic()
    result = execute_shell_command(
        command="sleep 30",
        argv=None,
        use_shell=True,
        timeout_seconds=1,
        max_output_chars=10_000,
    )
    assert result.timed_out is True
    assert result.cancelled is False
    assert result.executed_with_shell is True
    assert time.monotonic() - started < 5


@pytest.mark.skipif(os.name == "nt", reason="process-group cancel is POSIX")
def test_execute_shell_command_stops_on_cancel_and_reaps_grandchild() -> None:
    """ESC must stop shell_run immediately and kill nested OpenSRE-style children.

    The CI-agent onboarding skill used to ``shell_run`` ``uv run opensre
    integrations setup github``. That second process ignored the parent ESC
    and kept running until the laptop ran out of memory.
    """
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen("
        "[sys.executable, '-c', 'import time; time.sleep(60)']"
        ")\n"
        "print(f'GRAND:{child.pid}', flush=True)\n"
        "time.sleep(60)\n"
    )
    cancel = threading.Event()

    def _request_cancel() -> None:
        time.sleep(0.3)
        cancel.set()

    threading.Thread(target=_request_cancel, daemon=True).start()
    started = time.monotonic()
    result = execute_shell_command(
        command="nested-sleep",
        argv=[sys.executable, "-c", script],
        use_shell=False,
        timeout_seconds=8,
        max_output_chars=10_000,
        cancel_event=cancel,
    )
    elapsed = time.monotonic() - started
    assert result.cancelled is True
    assert result.timed_out is False
    assert elapsed < 4
    assert "GRAND:" in result.stdout
    grand_pid = int(result.stdout.strip().split("GRAND:", 1)[1].split()[0])
    _assert_pid_gone(grand_pid)


def test_execute_quoted_heredoc_through_shell() -> None:
    command = """python3 - <<'PY'
print("hello-heredoc")
PY"""
    parsed = parse_shell_command(command, is_windows=False)
    assert parsed.use_shell is True

    result = execute_shell_command(
        command=parsed.command,
        argv=parsed.argv,
        use_shell=parsed.use_shell,
        timeout_seconds=10,
        max_output_chars=10_000,
    )

    assert result.timed_out is False
    assert result.exit_code == 0
    assert "hello-heredoc" in result.stdout
