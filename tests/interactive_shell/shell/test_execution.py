"""Tests for structured REPL shell execution."""

from __future__ import annotations

import os
import shlex
import sys
import threading
import time
from pathlib import Path

import pytest

import tools.interactive_shell.shell.execution as shell_execution
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


@pytest.mark.skipif(os.name == "nt", reason="uses the POSIX shell fallback")
def test_shell_fallback_does_not_load_login_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SHELL", raising=False)

    assert shell_execution._shell_argv("pwd") == ["/bin/sh", "-c", "pwd"]


def test_execute_shell_command_reports_timeout() -> None:
    started = time.monotonic()
    result = execute_shell_command(
        command="sleep 30",
        cwd=str(Path.cwd()),
        timeout_seconds=1,
        max_output_chars=10_000,
    )
    assert result.timed_out is True
    assert result.cancelled is False
    assert result.executed_with_shell is True
    assert time.monotonic() - started < 5


@pytest.mark.skipif(os.name == "nt", reason="process-group cancel is POSIX")
def test_execute_shell_command_stops_on_cancel_and_reaps_grandchild(
    tmp_path: Path,
) -> None:
    """ESC must stop shell_run immediately and kill nested OpenSRE-style children.

    The CI-agent onboarding skill used to ``shell_run`` ``uv run opensre
    integrations setup github``. That second process ignored the parent ESC
    and kept running until the laptop ran out of memory.
    """
    pid_file = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen("
        "[sys.executable, '-c', 'import time; time.sleep(60)']"
        ")\n"
        f"marker = Path({str(pid_file)!r})\n"
        "temporary_marker = marker.with_suffix('.tmp')\n"
        "temporary_marker.write_text(str(child.pid))\n"
        "temporary_marker.replace(marker)\n"
        "time.sleep(60)\n"
    )
    cancel = threading.Event()
    cancel_requested_at: list[float] = []

    def _request_cancel() -> None:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        cancel_requested_at.append(time.monotonic())
        cancel.set()

    threading.Thread(target=_request_cancel, daemon=True).start()
    result = execute_shell_command(
        command=shlex.join([sys.executable, "-c", script]),
        cwd=str(Path.cwd()),
        timeout_seconds=8,
        max_output_chars=10_000,
        cancel_event=cancel,
    )
    finished_at = time.monotonic()
    assert result.cancelled is True
    assert result.timed_out is False
    assert finished_at - cancel_requested_at[0] < 4
    grand_pid = int(pid_file.read_text())
    _assert_pid_gone(grand_pid)


def test_execute_quoted_heredoc_through_shell() -> None:
    command = """python3 - <<'PY'
print("hello-heredoc")
PY"""
    parsed = parse_shell_command(command)

    result = execute_shell_command(
        command=parsed.command,
        cwd=str(Path.cwd()),
        timeout_seconds=10,
        max_output_chars=10_000,
    )

    assert result.timed_out is False
    assert result.exit_code == 0
    assert "hello-heredoc" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="uses POSIX shell syntax")
def test_execute_compact_shell_script_with_operator_and_redirect(tmp_path: Path) -> None:
    output_file = tmp_path / "second.txt"
    command = f"printf one&&printf two>{shlex.quote(str(output_file))}"
    parsed = parse_shell_command(command)

    result = execute_shell_command(
        command=parsed.command,
        cwd=str(tmp_path),
        timeout_seconds=10,
        max_output_chars=10_000,
    )

    assert result.exit_code == 0
    assert result.executed_with_shell is True
    assert result.stdout == "one"
    assert output_file.read_text() == "two"


def test_execute_shell_command_uses_explicit_working_directory(tmp_path: Path) -> None:
    result = execute_shell_command(
        command="pwd",
        cwd=str(tmp_path),
        timeout_seconds=10,
        max_output_chars=10_000,
    )

    assert result.exit_code == 0
    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()
