"""Tests for the per-PID resource probe (issue #1489)."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import psutil
import pytest

from tests.shared.product_sources import product_python_files
from tests.utils.polling import wait_until
from tools.system.fleet_monitoring.probe import (
    ProcessSnapshot,
    probe,
    process_has_open_codex_rollout,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PROBE_MODULE = _REPO_ROOT / "tools" / "system" / "fleet_monitoring" / "probe.py"
# Modules sanctioned to import psutil. Each must wrap it behind plain-value
# helpers so callers never touch psutil directly: probe.py for per-PID
# snapshots, probes.py for system-wide disk/memory session facts
# (config/ cannot import tools/, so it wraps psutil itself), and tree.py
# for supervised child-process lifecycle without exposing psutil types.
_PSUTIL_SANCTIONED = (
    _PROBE_MODULE,
    _REPO_ROOT / "config" / "runtime_metadata" / "probes.py",
    _REPO_ROOT / "infrastructure" / "process" / "tree.py",
)
_SOURCE_ROOTS = (
    "cli",
    "config",
    "core",
    "deployment",
    "integrations",
    "infrastructure",
    "services",
    "tools",
)


@pytest.fixture
def busy_process() -> Iterator[int]:
    """Spawn a subprocess burning CPU in a tight loop; yields its PID.
    The warmup wait ensures the process has accumulated enough CPU time for
    psutil.cpu_percent(interval>0) to measure a non-zero delta.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "while True: pass"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    process = psutil.Process(proc.pid)
    wait_until(
        lambda: sum(process.cpu_times()[:2]) > 0.0,
        timeout=2.0,
        interval=0.01,
    )

    yield proc.pid

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def test_probe_returns_snapshot_for_self() -> None:
    """Probing the current Python process must return a populated snapshot."""
    snap = probe(os.getpid(), cpu_interval=0.0)

    assert snap is not None
    assert isinstance(snap, ProcessSnapshot)
    assert snap.pid == os.getpid()
    assert snap.rss_mb > 0.0, "interpreter + pytest should occupy non-zero RSS"
    assert snap.status, "status should be a non-empty string (e.g. 'running')"
    assert isinstance(snap.started_at, datetime)
    assert snap.started_at.tzinfo == UTC, "started_at must be tz-aware UTC"
    if sys.platform != "win32":
        assert snap.num_fds is not None and snap.num_fds > 0, (
            "POSIX systems should always report a positive FD count"
        )


def test_probe_returns_none_for_missing_pid() -> None:
    """Probing a PID that does not exist returns None, never raises."""
    # 2**31 - 1 is far above any realistic allocated PID on Linux/macOS
    # (kernel.pid_max is typically 32768 or 4194304).
    assert probe(2**31 - 1, cpu_interval=0.0) is None


def test_probe_returns_none_for_access_denied_process() -> None:
    """``cpu_percent()`` and ``memory_info()`` raise ``psutil.AccessDenied``
    for processes owned by another user on macOS and on Linux setups
    with restricted ``/proc``. The wiring layer treats both that and a
    truly missing PID as "no snapshot this tick" — the function must
    never let ``AccessDenied`` escape.
    """
    with patch.object(
        psutil.Process,
        "memory_info",
        side_effect=psutil.AccessDenied(pid=os.getpid()),
    ):
        assert probe(os.getpid(), cpu_interval=0.0) is None


def test_process_has_open_codex_rollout_matches_rollout_jsonl() -> None:
    with patch.object(
        psutil.Process,
        "open_files",
        return_value=[
            SimpleNamespace(path="/tmp/unrelated.log"),
            SimpleNamespace(path="/Users/me/.codex/rollout-2026-05-17.jsonl"),
        ],
    ):
        assert process_has_open_codex_rollout(os.getpid()) is True


def test_process_has_open_codex_rollout_returns_false_when_inaccessible() -> None:
    with patch.object(
        psutil.Process,
        "open_files",
        side_effect=psutil.AccessDenied(pid=os.getpid()),
    ):
        assert process_has_open_codex_rollout(os.getpid()) is False


def test_psutil_is_not_imported_outside_sanctioned_modules() -> None:
    """``psutil`` must stay confined to the sanctioned wrapper modules so the
    dependency surface is explicit. A static scan over runtime package trees
    catches future regressions deterministically — runtime import-graph checks
    would be flaky against lazy-import patterns the codebase already uses
    elsewhere.
    """
    leaks: list[str] = []
    py_files: list[pathlib.Path] = []
    for root_name in _SOURCE_ROOTS:
        py_files.extend(product_python_files(_REPO_ROOT / root_name))
    for py_file in py_files:
        if py_file in _PSUTIL_SANCTIONED:
            continue
        text = py_file.read_text(encoding="utf-8")
        for needle in ("import psutil", "from psutil"):
            if needle in text:
                leaks.append(f"{py_file.relative_to(_REPO_ROOT)} contains {needle!r}")
                break

    assert not leaks, (
        "psutil leaked into modules other than the sanctioned wrappers "
        f"({', '.join(str(p.relative_to(_REPO_ROOT)) for p in _PSUTIL_SANCTIONED)}):\n  "
        + "\n  ".join(leaks)
    )


def test_probe_returns_nonzero_cpu_for_busy_process(busy_process: int) -> None:
    """Regression: cpu_percent(interval>0) returned 0.0 when called inside proc.oneshot()
    because both internal readings hit the same cache (#1950). Probing a busy subprocess
    with a positive interval must yield a non-zero value now that the call lives
    outside the oneshot block.
    """
    snap = probe(busy_process, cpu_interval=0.1)

    assert snap is not None
    assert snap.cpu_percent > 0.0
