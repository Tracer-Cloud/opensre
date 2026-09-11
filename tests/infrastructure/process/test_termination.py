"""Process-tree termination boundary tests."""

from __future__ import annotations

from types import SimpleNamespace

import psutil
import pytest

from infrastructure.process.termination import terminate_process_tree


def test_terminate_process_tree_stops_descendants_before_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def _process(name: str) -> SimpleNamespace:
        return SimpleNamespace(
            terminate=lambda: events.append(f"terminate:{name}"),
            kill=lambda: events.append(f"kill:{name}"),
        )

    child = _process("child")
    grandchild = _process("grandchild")
    root = _process("root")

    def _children(*, recursive: bool) -> list[SimpleNamespace]:
        assert recursive
        return [child, grandchild]

    root.children = _children
    wait_results = iter([([child, grandchild], [root]), ([root], [])])
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
        "terminate:grandchild",
        "terminate:child",
        "terminate:root",
        "kill:root",
    ]
