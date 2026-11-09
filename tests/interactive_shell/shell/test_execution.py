"""Tests for structured REPL shell execution."""

from __future__ import annotations

import contextlib
import os
import shlex
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from config.constants.terminal_host import BASH_EXPORTED_FUNCTION_ENV_PREFIX
from tools.interactive_shell.shell import execution as shell_execution
from tools.interactive_shell.shell.execution import execute_shell_command
from tools.interactive_shell.shell.output_capture import OutputStream, ShellOutputCapture


def _execute(
    command: str,
    *,
    cancel_event: threading.Event | None = None,
) -> shell_execution.ShellExecutionResult:
    return execute_shell_command(
        command=command,
        timeout_seconds=8,
        max_output_bytes=10_000,
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


def test_shell_argv_disables_windows_startup_and_delayed_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell_execution.os, "name", "nt")
    monkeypatch.setenv("COMSPEC", r"C:\attacker\cmd.exe")
    monkeypatch.setattr(
        shell_execution,
        "_windows_command_shell",
        lambda: r"C:\Windows\System32\cmd.exe",
    )

    assert shell_execution._shell_argv("echo ok") == (
        r'"C:\Windows\System32\cmd.exe" /d /v:off /s /c "echo ok"'
    )


def test_shell_argv_keeps_pwd_diagnostic_portable_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell_execution.os, "name", "nt")
    monkeypatch.setenv("COMSPEC", r"C:\attacker\cmd.exe")
    monkeypatch.setattr(
        shell_execution,
        "_windows_command_shell",
        lambda: r"C:\Windows\System32\cmd.exe",
    )

    assert shell_execution._shell_argv("pwd") == (
        r'"C:\Windows\System32\cmd.exe" /d /v:off /s /c "cd"'
    )


def test_shell_argv_preserves_windows_command_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell_execution.os, "name", "nt")
    monkeypatch.setattr(
        shell_execution,
        "_windows_command_shell",
        lambda: r"C:\Windows\System32\cmd.exe",
    )

    assert shell_execution._shell_argv('echo "quoted&value"') == (
        r'"C:\Windows\System32\cmd.exe" /d /v:off /s /c "echo "quoted&value""'
    )


def test_shell_environment_removes_exported_bash_functions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    function_name = f"{BASH_EXPORTED_FUNCTION_ENV_PREFIX}ls%%"
    monkeypatch.setenv(function_name, "() { touch /tmp/should-not-run; }")
    monkeypatch.setenv("OPENSRE_TEST_SENTINEL", "kept")

    child_env = shell_execution._shell_environment()

    assert function_name not in child_env
    assert child_env["OPENSRE_TEST_SENTINEL"] == "kept"


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
        max_output_bytes=10_000,
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_execute_shell_command_times_out_and_reaps_background_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.interactive_shell.shell import execution

    watch = execution.watch_subprocess_until_exit

    def watch_after_parent_exit(proc, **kwargs):
        # Pin the real race: the shell exits before ownership is inspected.
        proc.wait(timeout=5)
        return watch(proc, **kwargs)

    monkeypatch.setattr(execution, "watch_subprocess_until_exit", watch_after_parent_exit)
    marker = tmp_path / "background.pid"
    command = f"sleep 60 >/dev/null 2>&1 & echo $! > {shlex.quote(str(marker))}"
    background_pid: int | None = None

    try:
        result = execute_shell_command(
            command=command,
            timeout_seconds=1,
            max_output_bytes=10_000,
        )
        background_pid = int(marker.read_text())

        assert result.timed_out is True
        _assert_pid_gone(background_pid)
    finally:
        if background_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(background_pid, signal.SIGKILL)


def test_execute_quoted_heredoc_through_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHELL", raising=False)
    command = """python3 - <<'PY'
print("hello-heredoc")
PY"""

    result = _execute(command)

    assert result.timed_out is False
    assert result.exit_code == 0
    assert "hello-heredoc" in result.stdout


def test_chatty_command_preserves_final_error_and_exit_status() -> None:
    script = "print('START'); print('x' * 30000); print('FINAL_ERROR'); raise SystemExit(7)"

    result = _execute(shlex.join([sys.executable, "-c", script]))

    assert result.exit_code == 7
    assert result.truncated
    assert result.stdout.startswith("START")
    assert result.stdout.endswith("FINAL_ERROR\n")
    assert "truncated" in result.stdout


def test_pipe_capture_reads_partial_output_and_preserves_split_utf8() -> None:
    head_ready = threading.Event()
    tail_ready = threading.Event()

    class NotifyingCapture(ShellOutputCapture):
        def append(self, stream: OutputStream, text: str) -> None:
            super().append(stream, text)
            if text == "HEAD":
                head_ready.set()
            if "TAIL" in text:
                tail_ready.set()

    reader_fd, writer_fd = os.pipe()
    pipe = os.fdopen(reader_fd, "r", encoding="utf-8", errors="replace")
    capture = NotifyingCapture()
    reader = threading.Thread(target=shell_execution._drain_pipe, args=(pipe, capture, "stdout"))
    reader.start()
    try:
        encoded = "HEAD🙂TAIL".encode()
        os.write(writer_fd, encoded[:6])
        assert head_ready.wait(timeout=2.0), "capture waited for EOF or a full chunk"
        os.write(writer_fd, encoded[6:])
        assert tail_ready.wait(timeout=2.0)
    finally:
        os.close(writer_fd)
        reader.join(timeout=2.0)

    assert capture.snapshot()[0] == "HEAD🙂TAIL"


def test_both_streams_share_codex_capture_limit_without_losing_final_diagnostics() -> None:
    script = (
        "import sys; print('START', flush=True); print('x' * 700000, flush=True); "
        "print('y' * 700000, file=sys.stderr, flush=True); "
        "print('FINAL_ERROR', file=sys.stderr, flush=True)"
    )

    result = execute_shell_command(
        command=shlex.join([sys.executable, "-c", script]),
        timeout_seconds=8,
        max_output_bytes=2 * 1024 * 1024,
    )

    assert result.truncated
    assert "START" in result.stdout
    assert "FINAL_ERROR" in result.stderr
    assert "bytes omitted" in result.combined_output
    assert len(result.combined_output.encode("utf-8")) <= 1024 * 1024 + 100
