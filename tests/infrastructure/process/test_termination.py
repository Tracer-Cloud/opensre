"""Process-tree termination boundary tests."""

from __future__ import annotations

from types import SimpleNamespace

import psutil
import pytest

from infrastructure.process.termination import terminate_process_tree


def test_terminate_process_tree_freezes_root_and_collects_late_descendants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def _no_children(*, recursive: bool) -> list[SimpleNamespace]:
        assert recursive
        return []

    def _process(pid: int, name: str) -> SimpleNamespace:
        return SimpleNamespace(
            pid=pid,
            children=_no_children,
            suspend=lambda: events.append(f"suspend:{name}"),
            terminate=lambda: events.append(f"terminate:{name}"),
            kill=lambda: events.append(f"kill:{name}"),
        )

    child = _process(124, "child")
    grandchild = _process(125, "grandchild")
    late_child = _process(126, "late-child")
    root = _process(123, "root")
    scans = 0

    def _children(*, recursive: bool) -> list[SimpleNamespace]:
        nonlocal scans
        assert recursive
        scans += 1
        events.append(f"scan:{scans}")
        if scans == 1:
            return [child, grandchild]
        return [child, grandchild, late_child]

    root.children = _children
    wait_results = iter([([child, grandchild, late_child], [root]), ([root], [])])
    monkeypatch.setattr(psutil, "Process", lambda _pid: root)

    def _wait_procs(
        _processes: list[SimpleNamespace],
        timeout: float,
    ) -> tuple[list[SimpleNamespace], list[SimpleNamespace]]:
        assert timeout > 0
        return next(wait_results)

    monkeypatch.setattr(psutil, "wait_procs", _wait_procs)

    terminate_process_tree(123, grace_seconds=10, force_wait_seconds=5)

    assert events == [
        "suspend:root",
        "scan:1",
        "suspend:child",
        "suspend:grandchild",
        "scan:2",
        "suspend:late-child",
        "scan:3",
        "terminate:late-child",
        "terminate:grandchild",
        "terminate:child",
        "terminate:root",
        "kill:root",
    ]


def test_terminate_process_tree_scans_until_descendants_stop_appearing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def _no_children(*, recursive: bool) -> list[SimpleNamespace]:
        assert recursive
        return []

    def _process(pid: int, name: str) -> SimpleNamespace:
        return SimpleNamespace(
            pid=pid,
            children=_no_children,
            suspend=lambda: events.append(f"suspend:{name}"),
            terminate=lambda: events.append(f"terminate:{name}"),
            kill=lambda: events.append(f"kill:{name}"),
        )

    root = _process(123, "root")
    late_children = [_process(200 + index, f"late-{index}") for index in range(1, 10)]
    scans = 0

    def _children(*, recursive: bool) -> list[SimpleNamespace]:
        nonlocal scans
        assert recursive
        scans += 1
        events.append(f"scan:{scans}")
        return late_children[:scans]

    root.children = _children
    monkeypatch.setattr(psutil, "Process", lambda _pid: root)

    def _wait_procs(
        processes: list[SimpleNamespace], *, timeout: float
    ) -> tuple[list[SimpleNamespace], list[SimpleNamespace]]:
        assert timeout > 0
        return processes, []

    monkeypatch.setattr(psutil, "wait_procs", _wait_procs)

    terminate_process_tree(123, grace_seconds=10, force_wait_seconds=5)

    assert "suspend:late-9" in events
    assert "scan:10" in events
    assert events.index("terminate:late-9") < events.index("terminate:root")
