"""The API modules of ``core.agent_harness`` and the scanner that enforces them.

The harness is imported through a fixed set of API modules. This module is the
single definition of that set, shared by the border tests (which allow these
modules and no others) and by ``tests/core/agent_harness/test_harness_api.py``
(which pins their exported names), so the two cannot diverge.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.shared.api_border import ApiBorder, python_sources

HARNESS_PACKAGE = "core.agent_harness"

#: The ``core.agent_harness.spi`` role modules. Each is an API module; the
#: ``spi`` package itself is not.
SPI_ROLES: frozenset[str] = frozenset(
    {
        "activity",
        "session_goal",
        "session_state",
        "cancel",
        "accounting",
        "prompt_chrome",
        "integrations",
        "grounding",
        "defaults",
        "handoff",
        "task_plan",
    }
)

#: Every module through which the harness may be imported. Listed by exact name
#: rather than by prefix, so a future internal module under ``spi/`` does not
#: become part of the API by accident.
API_MODULES: frozenset[str] = frozenset(
    {
        HARNESS_PACKAGE,
        f"{HARNESS_PACKAGE}.ports",
        f"{HARNESS_PACKAGE}.runtime",
        f"{HARNESS_PACKAGE}.tools",
    }
    | {f"{HARNESS_PACKAGE}.spi.{role}" for role in SPI_ROLES}
)

#: The harness tier: one package, a fixed set of API modules.
_BORDER = ApiBorder(packages=(HARNESS_PACKAGE,), api_modules=API_MODULES)


def is_api_module(module: str) -> bool:
    """Return True when ``module`` is one of the harness API modules."""
    return _BORDER.is_api_module(module)


def is_harness_module(module: str) -> bool:
    """Return True when ``module`` is the harness package or any module inside it."""
    return _BORDER.owns(module)


def internal_harness_imports(tree: ast.AST) -> set[str]:
    """Return the harness modules ``tree`` imports that are not API modules."""
    return _BORDER.internal_imports(tree)


def internal_harness_imports_under(
    *roots: Path, exclude_parts: frozenset[str] = frozenset()
) -> set[str]:
    """Return every internal harness import across the Python sources under ``roots``."""
    return _BORDER.internal_imports_under(*roots, exclude_parts=exclude_parts)


def assert_internal_imports_match_allowlist(
    imported: set[str], allowlist: frozenset[str], *, package: str
) -> None:
    """Assert ``imported`` equals ``allowlist`` exactly; the allowlist may only shrink."""
    _BORDER.assert_matches_allowlist(imported, allowlist, consumer=package)


__all__ = [
    "HARNESS_PACKAGE",
    "API_MODULES",
    "SPI_ROLES",
    "assert_internal_imports_match_allowlist",
    "internal_harness_imports",
    "internal_harness_imports_under",
    "is_harness_module",
    "is_api_module",
    "python_sources",
]
