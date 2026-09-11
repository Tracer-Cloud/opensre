"""Unit tests for pure subprocess helpers in tools.interactive_shell.subprocess."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import tools.interactive_shell.subprocess as subprocess_tools
from tools.interactive_shell.subprocess import (
    read_diag,
    subprocess_env_with_width,
    terminate_child_process,
    watch_subprocess_until_exit,
)


def test_subprocess_env_with_width_reserves_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINES", raising=False)
    env = subprocess_env_with_width(columns=100, lines=30)
    assert env["COLUMNS"] == "81"
    assert env["LINES"] == "30"


def test_subprocess_env_with_width_preserves_existing_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINES", "24")
    env = subprocess_env_with_width(columns=100, lines=30)
    assert env["LINES"] == "24"


def test_read_diag_strips_ansi() -> None:
    with tempfile.SpooledTemporaryFile(max_size=4096) as buf:
        buf.write(b"\x1b[31merror\x1b[0m")
        buf.seek(0)
        assert read_diag(buf) == "error"


def test_terminate_child_process_noop_when_exited() -> None:
    proc = subprocess.Popen(["true"])
    proc.wait()
    terminate_child_process(proc)


def test_terminate_child_process_uses_tree_termination_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []
    proc = MagicMock()
    monkeypatch.setattr(subprocess_tools, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        subprocess_tools,
        "terminate_process_tree",
        lambda pid, **options: seen.append((pid, options)),
    )
    proc.pid = 123
    proc.poll.side_effect = [None, 0, 0]

    terminate_child_process(proc)

    assert seen == [
        (
            123,
            {
                "grace_seconds": subprocess_tools.SIGTERM_GRACE_SECONDS,
                "force_wait_seconds": 5,
            },
        )
    ]
    proc.terminate.assert_not_called()


def test_terminate_child_process_does_not_resolve_exited_windows_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminate_process_tree = MagicMock()
    proc = MagicMock(pid=123)
    proc.poll.return_value = 0
    monkeypatch.setattr(subprocess_tools, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        subprocess_tools,
        "terminate_process_tree",
        terminate_process_tree,
    )

    terminate_child_process(proc)

    terminate_process_tree.assert_not_called()
    proc.kill.assert_not_called()


@pytest.mark.skipif(os.name == "nt", reason="process-group cancel is POSIX")
def test_terminate_reaps_grandchild_that_ignores_sigterm() -> None:
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen(["
        "sys.executable, '-c',"
        "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'"
        "])\n"
        "print(f'GRAND:{child.pid}', flush=True)\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert proc.stdout is not None
    line = proc.stdout.readline()
    grand_pid = int(line.strip().split("GRAND:", 1)[1])
    terminate_child_process(proc)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.kill(grand_pid, 0)
        except OSError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"grandchild {grand_pid} still alive after terminate")
    assert proc.poll() is not None


def test_watch_subprocess_until_exit_on_cancel() -> None:
    proc = subprocess.Popen(["sleep", "30"])
    cancel = threading.Event()
    cancel.set()
    result = watch_subprocess_until_exit(
        proc,
        cancel_event=cancel,
        timeout_seconds=60,
    )
    assert result.cancelled
    assert result.terminated_by_watcher
