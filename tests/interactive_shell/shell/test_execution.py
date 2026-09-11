"""Tests for structured REPL shell execution."""

from __future__ import annotations

import os
import shlex
import sys
import threading
import time
from pathlib import Path

import pytest

from tools.interactive_shell.shell import execution as shell_execution
from tools.interactive_shell.shell.execution import execute_shell_command


def _execute(
    command: str,
    *,
    cancel_event: threading.Event | None = None,
) -> shell_execution.ShellExecutionResult:
    return execute_shell_command(
        command=command,
        timeout_seconds=8,
        max_output_chars=10_000,
        cancel_event=cancel_event,
    )


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


def test_shell_argv_uses_non_login_posix_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell_execution.os, "name", "posix")
    monkeypatch.delenv("SHELL", raising=False)

    assert shell_execution._shell_argv("printf ok") == ["/bin/sh", "-c", "printf ok"]


def test_shell_argv_does_not_load_configured_interactive_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell_execution.os, "name", "posix")
    monkeypatch.setenv("SHELL", "/bin/bash")

    assert shell_execution._shell_argv("printf ok") == ["/bin/sh", "-c", "printf ok"]


@pytest.mark.skipif(os.name == "nt", reason="Bash startup hook is POSIX-specific")
def test_execute_shell_command_does_not_source_bash_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "startup-hook-ran"
    hook = tmp_path / "bash-env"
    hook.write_text(f"touch {shlex.quote(str(marker))}\n")
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setenv("BASH_ENV", str(hook))

    result = _execute("pwd")

    assert result.exit_code == 0
    assert not marker.exists()


def test_execute_shell_command_reports_timeout() -> None:
    started = time.monotonic()

    result = execute_shell_command(
        command="sleep 30",
        timeout_seconds=1,
        max_output_chars=10_000,
    )

    assert result.timed_out is True
    assert result.cancelled is False
    assert result.executed_with_shell is True
    assert time.monotonic() - started < 5


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell syntax")
def test_execute_compact_shell_operators(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SHELL", raising=False)
    output_path = tmp_path / "result.txt"
    command = f"printf one&&printf two>{shlex.quote(str(output_path))}"

    result = _execute(command)

    assert result.exit_code == 0
    assert result.stdout == "one"
    assert output_path.read_text() == "two"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell syntax")
def test_shell_working_directory_does_not_persist_between_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SHELL", raising=False)

    changed = _execute(f"cd {shlex.quote(str(tmp_path))} && pwd")
    unchanged = _execute("pwd")

    assert changed.exit_code == 0
    assert changed.stdout.strip() == str(tmp_path)
    assert unchanged.exit_code == 0
    assert unchanged.stdout.strip() == str(Path.cwd())


@pytest.mark.skipif(os.name == "nt", reason="process-group cancel is POSIX")
def test_execute_shell_command_stops_on_cancel_and_reaps_grandchild(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ESC must stop the shell and all nested OpenSRE-style children."""
    monkeypatch.delenv("SHELL", raising=False)
    marker = tmp_path / "grandchild.pid"
    pending_marker = tmp_path / "grandchild.pid.pending"
    script = (
        "import os, pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen("
        "[sys.executable, '-c', 'import time; time.sleep(60)']"
        ")\n"
        f"pending = pathlib.Path({str(pending_marker)!r})\n"
        "pending.write_text(str(child.pid))\n"
        f"os.replace(pending, pathlib.Path({str(marker)!r}))\n"
        "time.sleep(60)\n"
    )
    cancel = threading.Event()

    def _request_cancel_after_child_starts() -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.01)
        cancel.set()

    threading.Thread(target=_request_cancel_after_child_starts, daemon=True).start()
    started = time.monotonic()
    result = _execute(shlex.join([sys.executable, "-c", script]), cancel_event=cancel)
    elapsed = time.monotonic() - started

    assert result.cancelled is True
    assert result.timed_out is False
    assert elapsed < 4
    grand_pid = int(marker.read_text())
    _assert_pid_gone(grand_pid)


def test_execute_quoted_heredoc_through_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHELL", raising=False)
    command = """python3 - <<'PY'
print("hello-heredoc")
PY"""

    result = _execute(command)

    assert result.timed_out is False
    assert result.exit_code == 0
    assert "hello-heredoc" in result.stdout
