"""Cron handoff must survive the interactive host's interpreter shutdown."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path

import psutil
import pytest


@pytest.mark.parametrize("timeout", [None, 0.0])
def test_kept_cli_output_is_capped_and_later_bytes_are_discarded(
    tmp_path: Path, timeout: float | None
) -> None:
    from infrastructure.process.cli_handoff import _CAPTURE_LIMIT_BYTES
    from surfaces.interactive_shell.command_registry import cli_parity

    finished = tmp_path / "finished"
    child = (
        "import pathlib, sys\n"
        "sys.stdout.write('x' * 2_000_000); sys.stdout.flush()\n"
        "sys.stderr.write('y' * 2_000_000); sys.stderr.flush()\n"
        f"pathlib.Path({str(finished)!r}).write_text('done')\n"
        "sys.exit(7)\n"
    )
    command = [sys.executable, "-c", child]
    if timeout is None:
        result = cli_parity._run_captured_keep_running(
            command, timeout=None, env=os.environ.copy(), detach_on_shutdown=False
        )
        assert result.returncode == 7
        assert len(result.stdout) == _CAPTURE_LIMIT_BYTES
        assert len(result.stderr) == _CAPTURE_LIMIT_BYTES
    else:
        with pytest.raises(subprocess.TimeoutExpired) as captured:
            cli_parity._run_captured_keep_running(
                command, timeout=timeout, env=os.environ.copy(), detach_on_shutdown=True
            )
        assert not captured.value.stdout
        assert not captured.value.stderr
        cli_parity.shutdown_kept_cli_commands()
    assert finished.read_text() == "done"


def test_foreground_snapshot_preserves_child_write_offset() -> None:
    from surfaces.interactive_shell.command_registry.cli_parity import _captured_file_snapshot

    with tempfile.NamedTemporaryFile() as output:
        output.write(b"first\n")
        output.flush()
        assert _captured_file_snapshot(output.name) == b"first\n"
        assert output.tell() == len(b"first\n")
        output.write(b"second\n")
        output.flush()
        assert _captured_file_snapshot(output.name) == b"first\nsecond\n"


def test_cron_tick_finishes_after_repl_process_exits(tmp_path: Path) -> None:
    """The host exits while the tick waits, then the orphan writes beyond pipe capacity."""
    release = tmp_path / "release"
    finished = tmp_path / "finished"
    pid_file = tmp_path / "child-pid"
    child = (
        "import os, pathlib, sys, time\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        f"while not pathlib.Path({str(release)!r}).exists(): time.sleep(0.01)\n"
        "sys.stdout.write('x' * 200_000); sys.stdout.flush()\n"
        "sys.stderr.write('y' * 200_000); sys.stderr.flush()\n"
        f"pathlib.Path({str(finished)!r}).write_text('done')\n"
    )
    host = (
        "import sys\n"
        "from rich.console import Console\n"
        "from surfaces.interactive_shell.session import Session\n"
        "from surfaces.interactive_shell.command_registry import cli_parity, cron_cmds\n"
        f"cli_parity.build_opensre_cli_argv = lambda _: [sys.executable, '-c', {child!r}]\n"
        "cron_cmds._RUN_FOREGROUND_SECONDS = 0.05\n"
        "session = Session()\n"
        "session.record('slash', '/cron run synthetic', ok=True)\n"
        "cli_parity._cmd_cron(session, Console(), ['run', 'synthetic'])\n"
        "assert session.history[-1]['slash_outcome'] == 'still_running'\n"
        "print('host exited', flush=True)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", host], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        output, errors = process.communicate(timeout=10)
        assert process.returncode == 0, errors
        assert "host exited" in output
        assert "Still running in the background" in output
        assert not finished.exists()
        release.touch()
        deadline = time.monotonic() + 10
        while not finished.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert finished.read_text() == "done"
    finally:
        release.touch()
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
        if pid_file.exists():
            with suppress(psutil.NoSuchProcess):
                psutil.Process(int(pid_file.read_text())).kill()
