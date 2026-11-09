"""Scan a package's consumers for imports that bypass its API modules.

A tier is imported through a fixed set of API modules; everything else inside it
is internal. This scanner reports the internal imports a consumer makes, so a
border test can hold that set to an allowlist that only shrinks.

Parameterised by tier because the rule is the same for each: ``core.agent_harness``
(see :mod:`tests.shared.harness_api`) and the tool tier
(:mod:`tests.shared.tool_api`) both bind to it rather than carrying a copy.
"""

from __future__ import annotations

import ast
import functools
import importlib
from dataclasses import dataclass
from pathlib import Path

from tests.shared.product_sources import is_vendored

_DYNAMIC_IMPORT_FUNCTIONS = frozenset({"import_module", "__import__"})


def python_sources(root: Path, *, exclude_parts: frozenset[str] = frozenset()) -> list[Path]:
    """Return the shipped Python files under ``root``, excluding ``exclude_parts``.

    Vendored trees are skipped via :func:`tests.shared.product_sources.is_vendored`:
    a virtualenv created inside a package (``config/.venv`` has happened) holds
    third-party sources this scanner must not read, some of which the current
    interpreter cannot even parse.
    """
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if not is_vendored(path.relative_to(root)) and not (exclude_parts & set(path.parts))
    ]


def _called_function_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


@functools.cache
def _exported_names(module: str) -> frozenset[str]:
    """The names an API module exports in ``__all__``; empty when it exports none."""
    return frozenset(getattr(importlib.import_module(module), "__all__", ()))


@dataclass(frozen=True, slots=True)
class ApiBorder:
    """One tier: the packages it spans and the modules it may be imported through."""

    #: Package roots that belong to the tier, e.g. ``("core.tool", "core.tool_framework")``.
    packages: tuple[str, ...]
    #: Modules a consumer may import. Exact names, so a new submodule under an
    #: API package does not join the API by accident.
    api_modules: frozenset[str]

    def owns(self, module: str) -> bool:
        """True when ``module`` is one of the tier's packages or lives inside one."""
        return any(module == pkg or module.startswith(pkg + ".") for pkg in self.packages)

    def is_api_module(self, module: str) -> bool:
        return module in self.api_modules

    def internal_imports(self, tree: ast.AST) -> set[str]:
        """Return the tier modules ``tree`` imports that are not API modules.

        Covers ``import x``, ``from x import y`` and dynamic imports
        (``import_module("x")``). For ``from <api module> import y``, a name the
        API module does not export in ``__all__`` is treated as an import of the
        internal submodule ``<api module>.y``.
        """
        internal: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                internal.update(
                    alias.name
                    for alias in node.names
                    if self.owns(alias.name) and not self.is_api_module(alias.name)
                )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level != 0 or not self.owns(module):
                    continue
                if not self.is_api_module(module):
                    internal.add(module)
                    continue
                exported = _exported_names(module)
                internal.update(
                    f"{module}.{alias.name}" for alias in node.names if alias.name not in exported
                )
            elif (
                isinstance(node, ast.Call)
                and _called_function_name(node) in _DYNAMIC_IMPORT_FUNCTIONS
            ):
                for argument in node.args[:1]:
                    if (
                        isinstance(argument, ast.Constant)
                        and isinstance(argument.value, str)
                        and self.owns(argument.value)
                        and not self.is_api_module(argument.value)
                    ):
                        internal.add(argument.value)
        return internal

    def internal_imports_under(
        self, *roots: Path, exclude_parts: frozenset[str] = frozenset()
    ) -> set[str]:
        """Return every internal import of this tier across the sources under ``roots``."""
        imported: set[str] = set()
        for root in roots:
            for path in python_sources(root, exclude_parts=exclude_parts):
                source = path.read_text(encoding="utf-8")
                imported |= self.internal_imports(ast.parse(source, filename=str(path)))
        return imported

    def assert_matches_allowlist(
        self, imported: set[str], allowlist: frozenset[str], *, consumer: str
    ) -> None:
        """Assert ``imported`` equals ``allowlist`` exactly.

        A module not on the allowlist is a new internal dependency and fails; a
        module on the allowlist that is no longer imported must be removed, so
        the allowlist can only shrink.
        """
        added = sorted(imported - allowlist)
        assert added == [], (
            f"{consumer} imports internals not on the allowlist: {added}. "
            f"Import through an API module ({', '.join(sorted(self.api_modules))}) "
            "or export the name from the appropriate one."
        )
        stale = sorted(allowlist - imported)
        assert stale == [], (
            f"{consumer} no longer imports {stale}; remove them from the allowlist "
            "so it keeps shrinking."
        )


__all__ = ["ApiBorder", "python_sources"]
