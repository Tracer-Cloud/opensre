"""Terminate a bounded repair including coding CLIs that create their own process groups."""

from __future__ import annotations

import os
import threading
import time

import psutil


def _kill_descendants(process: psutil.Process) -> None:
    """Kill descendants even when a coding CLI has created a separate POSIX session."""
    try:
        children = process.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    for child in children:
        try:
            child.suspend()
        except psutil.NoSuchProcess:
            continue
    for child in reversed(children):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            continue
    psutil.wait_procs(children, timeout=2)


def stop_worker(pid: int) -> None:
    """Stop creation of descendants before killing the entire worker tree."""
    try:
        process = psutil.Process(pid)
        process.suspend()
        _kill_descendants(process)
        process.kill()
    except psutil.NoSuchProcess:
        return


def start_watchdog(deadline: float) -> threading.Event:
    """Bound an orphan worker when the scheduler process dies or stops supervising it."""
    stop = threading.Event()
    parent = os.getppid()

    def watch() -> None:
        while not stop.wait(0.25):
            if os.getppid() != parent or time.time() >= deadline:
                _kill_descendants(psutil.Process())
                os._exit(1)

    threading.Thread(target=watch, daemon=True, name="worker-deadline").start()
    return stop
