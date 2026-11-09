"""Ban constant-condition toggles that disable real product logic.

Never ship ``if False and …`` / ``if True or …`` / ``if False:`` to mute a
branch while “fixing tests”. Delete the code, gate on a real named flag, or
fix the underlying failure — do not leave dead boolean toggles in product
packages.
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


def _is_test_path(path: Path) -> bool:
    return "tests" in path.parts or path.name.startswith("test_")


def _bool_const(node: ast.AST) -> bool | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _toggle_offenses(tree: ast.AST) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []

    class _Visitor(ast.NodeVisitor):
        def visit_If(self, node: ast.If) -> None:
            self._check_test(node.test, node.lineno)
            self.generic_visit(node)

        def _check_test(self, test: ast.AST, lineno: int) -> None:
            alone = _bool_const(test)
            if alone is not None:
                hits.append((lineno, f"if {alone}"))
                return
            if not isinstance(test, ast.BoolOp):
                return
            op = "and" if isinstance(test.op, ast.And) else "or"
            for value in test.values:
                flag = _bool_const(value)
                if flag is False and op == "and":
                    hits.append((lineno, "… and False"))
                elif flag is True and op == "or":
                    hits.append((lineno, "… or True"))

    _Visitor().visit(tree)
    return hits


@pytest.mark.parametrize("package", _PRODUCT_ROOTS)
def test_product_packages_have_no_constant_condition_toggles(package: str) -> None:
    root = _REPO_ROOT / package
    if not root.is_dir():
        pytest.skip(f"{package}/ missing")

    offenders: list[str] = []
    for path in product_python_files(root):
        if _is_test_path(path):
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            offenders.append(f"{path.relative_to(_REPO_ROOT)}: syntax error: {exc}")
            continue
        for lineno, kind in _toggle_offenses(tree):
            offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {kind}")

    assert offenders == [], (
        "Constant-condition toggles disable real logic (e.g. cancel short-circuit). "
        "Remove the toggle; keep or delete the branch for real:\n" + "\n".join(offenders)
    )
