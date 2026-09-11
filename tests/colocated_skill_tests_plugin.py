"""Keep pytest's importlib mode from re-executing the skills package.

Tests colocated under hyphenated skill directories are imported by file path,
and pytest spec-imports any ancestor package still missing from
``sys.modules`` — a second execution of a package that is already importable.
Importing the package before collection prevents that; the collection-finish
hook fails the session if a guarded submodule is not the object its parent
package binds.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

import core.agent_harness.prompts.skills  # noqa: F401  (eager import is the fix)

_GUARDED_PACKAGE = "core.agent_harness.prompts"


def _duplicated_submodules() -> list[str]:
    """Submodules under the guarded package whose parent binds a different object."""
    prefix = _GUARDED_PACKAGE + "."
    duplicated: list[str] = []
    for name, module in list(sys.modules.items()):
        if not name.startswith(prefix) or module is None:
            continue
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if not isinstance(parent, ModuleType):
            continue
        if getattr(parent, child, None) is not module:
            duplicated.append(name)
    return sorted(duplicated)


def pytest_collection_finish(session: pytest.Session) -> None:
    duplicated = _duplicated_submodules()
    if not duplicated:
        return
    session.shouldfail = (
        "collection re-executed an already imported package; these modules are "
        "not the object their parent package binds: " + ", ".join(duplicated)
    )
