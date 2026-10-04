"""A workspace scan started at the menu answers the matching tool call once, and only that."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import tools.system.workspace_git_scan.tool as scan_tool
from core.agent_harness.tools.tool_context import (
    ACTION_TOOL_CONTEXT_RESOURCE_KEY,
    ActionToolScope,
)
from core.tool.contracts import AgentToolContext
from tools.system.workspace_git_scan import prefetch_workspace_scan, scan_local_git_workspace
from tools.system.workspace_git_scan.scan import ScanStop, WorkspaceSnapshot

_WAIT_SECONDS = 5.0


class _Scans:
    """Fake ``scan_workspace``: records each scan's arguments and honours ``should_stop``."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, int, frozenset[Path]]] = []
        self.release = threading.Event()
        self.release.set()
        self.failures_left = 0

    def __call__(
        self,
        root: Path,
        *,
        days: int,
        skip_paths: frozenset[Path],
        should_stop: Callable[[], bool] | None = None,
        **_kwargs: Any,
    ) -> WorkspaceSnapshot:
        self.calls.append((root, days, frozenset(skip_paths)))
        if should_stop is not None and should_stop():
            return WorkspaceSnapshot(root=str(root), days=days, stop_reason=ScanStop.CANCELLED)
        assert self.release.wait(_WAIT_SECONDS)
        if self.failures_left:
            self.failures_left -= 1
            raise OSError("disk went away")
        return WorkspaceSnapshot(root=str(root), days=days)


class _EscPressedConsole:
    """The turn console after the user pressed ESC."""

    cancel_requested = True

    def print(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a cancelled scan renders nothing")


@pytest.fixture
def scans(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Scans:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    fake = _Scans()
    monkeypatch.setattr(scan_tool, "scan_workspace", fake)
    return fake


def test_a_prefetched_scan_answers_the_matching_call_once(scans: _Scans) -> None:
    # Arrange
    assert prefetch_workspace_scan()

    # Act
    first = scan_local_git_workspace()
    second = scan_local_git_workspace()

    # Assert: the first call reused the prefetch with the tool's own arguments.
    assert len(scans.calls) == 2
    root, days, skip_paths = scans.calls[0]
    assert (root, days) == (Path.home().resolve(), 30)
    assert skip_paths  # the default skip paths are never dropped for a prefetch
    assert scans.calls[0] == scans.calls[1]
    assert first == second


def test_other_arguments_or_a_failed_prefetch_scan_live(scans: _Scans) -> None:
    # Arrange: a 30-day prefetch, then a call for another window.
    assert prefetch_workspace_scan()
    other = scan_local_git_workspace(days=7)
    reused = scan_local_git_workspace()
    scans.failures_left = 1
    assert prefetch_workspace_scan()

    # Act: the failed prefetch leaves the call to scan on its own.
    live = scan_local_git_workspace()

    # Assert: two prefetches and two live scans; the reused call scanned nothing.
    assert sorted(days for _root, days, _skip in scans.calls) == [7, 30, 30, 30]
    assert other["days"] == 7
    assert reused["success"] is True
    assert live["success"] is True


def test_cancel_while_the_prefetch_runs_returns_cancelled_without_waiting(
    scans: _Scans,
) -> None:
    # Arrange: the prefetched scan is still running when the tool is called.
    scans.release.clear()
    assert prefetch_workspace_scan()
    scope = ActionToolScope(session=None, console=_EscPressedConsole())
    context = AgentToolContext(
        resolved_integrations={}, resources={ACTION_TOOL_CONTEXT_RESOURCE_KEY: scope}
    )

    # Act
    result = scan_local_git_workspace(context=context)
    scans.release.set()

    # Assert
    assert result["cancelled"] is True
    assert result["stop_reason"] == ScanStop.CANCELLED.value
