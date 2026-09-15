"""Cross-platform process-tree termination primitives."""

from __future__ import annotations

import contextlib
from time import monotonic
from typing import Any

_FREEZE_DISCOVERY_TIMEOUT_SECONDS = 1.0


def _suspend_or_terminate(process: Any, *, psutil: Any) -> tuple[bool, bool]:
    """Stop a process; return ``(stopped, terminated)``."""
    try:
        process.suspend()
    except (psutil.Error, OSError):
        try:
            process.terminate()
        except (psutil.Error, OSError):
            return False, False
        return True, True
    return True, False


def _force_kill(processes: list[Any], *, psutil: Any) -> None:
    """Best-effort immediate stop for processes that may still spawn children."""
    for process in processes:
        with contextlib.suppress(psutil.Error, OSError):
            process.kill()


def _discover_descendants(root: Any, descendants: dict[int, Any], *, psutil: Any) -> list[Any]:
    """Collect descendants not already present in *descendants*."""
    discovered: list[Any] = []
    for parent in (root, *descendants.values()):
        try:
            children = parent.children(recursive=True)
        except (psutil.Error, OSError):
            continue
        for child in children:
            if child.pid == root.pid or child.pid in descendants:
                continue
            descendants[child.pid] = child
            discovered.append(child)
    return discovered


def _freeze_descendants(root: Any, *, psutil: Any) -> tuple[list[Any], bool]:
    """Stop descendants until stable, or report a deadline that needs a forced stop."""
    descendants: dict[int, Any] = {}
    deadline = monotonic() + _FREEZE_DISCOVERY_TIMEOUT_SECONDS
    while monotonic() < deadline:
        discovered = _discover_descendants(root, descendants, psutil=psutil)
        if not discovered:
            return list(descendants.values()), False
        for process in discovered:
            stopped, _ = _suspend_or_terminate(process, psutil=psutil)
            if not stopped:
                # A process that refused both cooperative stops can keep
                # extending the tree. Kill it immediately, then keep scanning
                # until the deadline for anything it created just beforehand.
                _force_kill([process], psutil=psutil)
    return list(descendants.values()), True


def terminate_process_tree(
    pid: int,
    *,
    grace_seconds: float,
    force_wait_seconds: float,
) -> None:
    """Freeze and terminate a process tree through psutil."""
    import psutil

    if pid <= 0:
        return
    try:
        root = psutil.Process(pid)
    except (psutil.Error, OSError):
        return

    # Freeze the root before inspecting descendants so it cannot add children
    # outside the snapshot. If suspension is unavailable, terminate it first.
    _, root_terminated = _suspend_or_terminate(root, psutil=psutil)

    descendants, discovery_timed_out = _freeze_descendants(root, psutil=psutil)
    if discovery_timed_out:
        # Stop every known spawner before the final recursive snapshot. This
        # closes the last-scan race where a child appears between discovery and
        # forced cleanup, while retaining a hard deadline for cancellation.
        _force_kill(list(reversed(descendants)), psutil=psutil)
        final_descendants: dict[int, Any] = {process.pid: process for process in descendants}
        _force_kill(
            _discover_descendants(root, final_descendants, psutil=psutil),
            psutil=psutil,
        )
        force_processes: list[Any] = [*reversed(final_descendants.values()), root]
        _force_kill([root], psutil=psutil)
        with contextlib.suppress(psutil.Error, OSError):
            psutil.wait_procs(force_processes, timeout=force_wait_seconds)
        return

    processes: list[Any] = [*reversed(descendants), root]
    for process in reversed(descendants):
        with contextlib.suppress(psutil.Error, OSError):
            process.terminate()
    if not root_terminated:
        with contextlib.suppress(psutil.Error, OSError):
            root.terminate()

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
