"""Whether a process id is still running.

A leaf: both supervision and component status need it, and putting it in
either would make them import each other.
"""

from __future__ import annotations

import contextlib
import os


def process_is_alive(pid: int) -> bool:
    """True while ``pid`` exists, reaping it first when it is our own child."""
    with contextlib.suppress(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)  # a dead child stays visible until reaped
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # exists, owned by another user
    return True


__all__ = ["process_is_alive"]
