"""Daemon: pidfile + spawn + stop for the detached gateway process.

Supervises a child whose output is captured in ``~/.opensre/gateway/gateway.log``
and whose PID is tracked in ``gateway.pid``.

This is process supervision, not the task scheduler. The caller supplies the
child's ``argv``. This module never imports CLI, never names
``surfaces.gateway_entry``, and never starts ``infrastructure.scheduling.scheduler`` itself.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections import deque
from collections.abc import Sequence
from pathlib import Path

from config.constants import OPENSRE_HOME_DIR
from gateway.core.process.component_status import clear_component_status
from gateway.core.process.liveness import process_is_alive

GATEWAY_LOG_FILE: Path = OPENSRE_HOME_DIR / "gateway" / "gateway.log"
GATEWAY_PID_FILE: Path = OPENSRE_HOME_DIR / "gateway" / "gateway.pid"


def gateway_daemon_pid() -> int | None:
    """Return the live daemon PID, clearing a stale pidfile on the way."""
    try:
        pid = int(GATEWAY_PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    if process_is_alive(pid):
        return pid
    GATEWAY_PID_FILE.unlink(missing_ok=True)
    return None


def start_gateway_daemon(*, argv: Sequence[str], startup_wait: float = 2.0) -> tuple[bool, str]:
    """Spawn ``argv`` as a detached background gateway process.

    ``argv`` is required: the surface starting the gateway owns which
    composition root runs, so this module never names one.

    Returns ``(ok, message)``. Starting an already-running gateway is a no-op
    success; a child that dies during ``startup_wait`` is a failure and the
    message carries the log tail.
    """
    if (pid := gateway_daemon_pid()) is not None:
        return True, f"OpenSRE gateway already running (pid {pid})."

    GATEWAY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with GATEWAY_LOG_FILE.open("ab") as log:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    GATEWAY_PID_FILE.write_text(f"{process.pid}\n")

    deadline = time.monotonic() + startup_wait
    while time.monotonic() < deadline and process.poll() is None:
        time.sleep(0.1)
    if process.poll() is not None:
        GATEWAY_PID_FILE.unlink(missing_ok=True)
        tail = read_gateway_log_tail(10) or "(log empty)"
        return False, f"OpenSRE gateway exited during startup:\n{tail}"
    return True, f"OpenSRE gateway started (pid {process.pid})."


def stop_gateway_daemon(*, timeout: float = 10.0) -> tuple[bool, str]:
    """SIGTERM the daemon, escalating to SIGKILL when it exceeds *timeout*."""
    pid = gateway_daemon_pid()
    if pid is None:
        return True, "OpenSRE gateway is not running."

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_is_alive(pid):
            _clear_runtime_files()
            return True, f"OpenSRE gateway stopped (pid {pid})."
        time.sleep(0.2)

    # A long-poll or in-flight turn can outlive the graceful window — force it.
    os.kill(pid, signal.SIGKILL)
    time.sleep(0.5)
    if process_is_alive(pid):
        return False, f"OpenSRE gateway (pid {pid}) survived SIGKILL."
    _clear_runtime_files()
    return True, f"OpenSRE gateway force-killed after {timeout:g}s (pid {pid})."


def _clear_runtime_files() -> None:
    GATEWAY_PID_FILE.unlink(missing_ok=True)
    clear_component_status()


def read_gateway_log_tail(lines: int = 50) -> str:
    """Return the last *lines* of the gateway log ('' when there is none)."""
    try:
        with GATEWAY_LOG_FILE.open("r", errors="replace") as log:
            return "".join(deque(log, maxlen=lines)).rstrip("\n")
    except OSError:
        return ""


__all__ = [
    "GATEWAY_LOG_FILE",
    "GATEWAY_PID_FILE",
    "gateway_daemon_pid",
    "read_gateway_log_tail",
    "start_gateway_daemon",
    "stop_gateway_daemon",
]
