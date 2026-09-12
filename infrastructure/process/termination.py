"""Cross-platform process-tree termination primitives."""

from __future__ import annotations

import contextlib
from time import monotonic
from typing import Any

_FREEZE_DISCOVERY_TIMEOUT_SECONDS = 1.0


def _suspend_or_terminate(process: Any, *, psutil: Any) -> bool:
    """Stop a process from spawning; return whether termination was used."""
    try:
        process.suspend()
    except (psutil.Error, OSError):
        try:
            process.terminate()
        except (psutil.Error, OSError):
            return False
        return True
    return False


def _freeze_descendants(root: Any, *, psutil: Any) -> tuple[list[Any], bool]:
    """Stop descendants until stable, or report a deadline that needs a forced stop."""
    descendants: dict[int, Any] = {}
    deadline = monotonic() + _FREEZE_DISCOVERY_TIMEOUT_SECONDS
    while monotonic() < deadline:
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
        if not discovered:
            return list(descendants.values()), False
        for process in discovered:
            _suspend_or_terminate(process, psutil=psutil)
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
    root_terminated = _suspend_or_terminate(root, psutil=psutil)

    descendants, discovery_timed_out = _freeze_descendants(root, psutil=psutil)
    processes: list[Any] = [*reversed(descendants), root]
    if discovery_timed_out:
        for process in processes:
            with contextlib.suppress(psutil.Error, OSError):
                process.kill()
        with contextlib.suppress(psutil.Error, OSError):
            psutil.wait_procs(processes, timeout=force_wait_seconds)
        return

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
