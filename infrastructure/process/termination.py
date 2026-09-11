"""Cross-platform process-tree termination primitives."""

from __future__ import annotations

import contextlib
from typing import Any


def terminate_process_tree(
    pid: int,
    *,
    grace_seconds: float,
    force_wait_seconds: float,
) -> None:
    """Terminate a process and its descendants through psutil."""
    import psutil

    if pid <= 0:
        return
    try:
        root = psutil.Process(pid)
        processes: list[Any] = [*reversed(root.children(recursive=True)), root]
    except (psutil.Error, OSError):
        return

    for process in processes:
        with contextlib.suppress(psutil.Error, OSError):
            process.terminate()

    alive = processes
    with contextlib.suppress(psutil.Error, OSError):
        _, alive = psutil.wait_procs(processes, timeout=grace_seconds)

    for process in alive:
        with contextlib.suppress(psutil.Error, OSError):
            process.kill()
    if alive:
        with contextlib.suppress(psutil.Error, OSError):
            psutil.wait_procs(alive, timeout=force_wait_seconds)


__all__ = ["terminate_process_tree"]
