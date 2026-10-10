"""Cron handoff must survive the interactive host's interpreter shutdown."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path

import psutil


def test_foreground_snapshot_preserves_child_write_offset() -> None:
    from surfaces.interactive_shell.command_registry.cli_parity import _captured_file_snapshot

    with tempfile.NamedTemporaryFile() as output:
        output.write(b"first\n")
        output.flush()
        assert _captured_file_snapshot(output) == b"first\n"
        assert output.tell() == len(b"first\n")
        output.write(b"second\n")
        output.flush()
        assert _captured_file_snapshot(output) == b"first\nsecond\n"


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
