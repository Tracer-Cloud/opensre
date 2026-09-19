"""Every turn-gate permit must be taken through the capacity context managers.

``turn_slot`` and ``queued_turn_slot`` pair ``acquire`` with ``release`` in a
``finally``. A hand-rolled ``gate.try_acquire()`` anywhere else is one missing
``finally`` away from a permanently leaked slot, and at the SMALL profile's
limit of one permit a leaked slot is a process that answers "at capacity"
forever — no crash, no restart, just a task that looks healthy and busy.

The behavior itself is pinned in ``tests/infrastructure/test_turn_capacity.py``;
this guard stops a new caller from reintroducing the hand-rolled pairing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.shared.product_sources import product_python_files

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PRODUCT_ROOTS = (
    "bootstrap",
    "config",
    "core",
    "gateway",
    "integrations",
    "infrastructure",
    "surfaces",
    "tools",
)

#: The module that owns the two policies, and so owns the only acquire/release pair.
_SLOTS_MODULE = Path("infrastructure/process/turn_capacity/slots.py")

_PERMIT_METHODS = frozenset({"try_acquire", "acquire", "release"})


def _is_test_path(path: Path) -> bool:
    return "tests" in path.parts or path.name.startswith("test_")


def _gate_permit_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """Permit calls made on something named like a turn gate.

    Keyed on the receiver rather than the method name: ``release`` alone is far
    too common (locks, semaphores, ``platform.release()``) to flag on its own.
    """
    hits: list[tuple[int, str]] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _PERMIT_METHODS:
                receiver = ast.unparse(func.value)
                if "gate" in receiver.lower():
                    hits.append((node.lineno, f"{receiver}.{func.attr}()"))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return hits


@pytest.mark.parametrize("package", _PRODUCT_ROOTS)
def test_turn_gate_permits_are_only_taken_inside_the_capacity_policies(package: str) -> None:
    root = _REPO_ROOT / package
    if not root.is_dir():
        pytest.skip(f"{package}/ missing")

    offenders: list[str] = []
    for path in product_python_files(root):
        if _is_test_path(path):
            continue
        relative = path.relative_to(_REPO_ROOT)
        if relative == _SLOTS_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, call in _gate_permit_calls(tree):
            offenders.append(f"{relative}:{lineno}: {call}")

    assert offenders == [], (
        "Take a turn slot through turn_slot() / queued_turn_slot() so the permit is "
        "released in a finally. A hand-paired acquire leaks the slot on any abnormal "
        "exit:\n" + "\n".join(offenders)
    )
