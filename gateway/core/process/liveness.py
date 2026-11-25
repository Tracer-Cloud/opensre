"""Whether a process id is still running.

A leaf: both supervision and component status need it, and putting it in
either would make them import each other.
"""

from __future__ import annotations

import contextlib
import os

from infrastructure.process.tree import process_is_running


def process_is_alive(pid: int) -> bool:
    """Return whether ``pid`` is still running, reaping exited children we own."""
    if pid <= 0:
        return False
    if os.name == "posix":
        with contextlib.suppress(ChildProcessError):
            if os.waitpid(pid, os.WNOHANG)[0]:
                return False
    return process_is_running(pid)


__all__ = ["process_is_alive"]
