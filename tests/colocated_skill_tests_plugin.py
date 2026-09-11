"""Keep pytest's importlib mode from re-executing the skills package.

Tests colocated beside a ``SKILL.md`` live under hyphenated directories that
``import_module`` cannot resolve, so pytest imports them by file path and first
spec-imports every ancestor package that is missing from ``sys.modules``.
Executing ``core/agent_harness/prompts/__init__.py`` that way imports
``skills`` through the regular import system, after which pytest executes
``skills/__init__.py`` a second time as a fresh module object. That copy never
receives its ``loader`` attribute (the submodule was already loaded, so the
import system does not bind it again) and it is the copy left registered, so
``monkeypatch.setattr("core.agent_harness.prompts.skills.loader.x", ...)``
fails on any xdist worker that collected a colocated test before importing
the package normally.

Importing the package here, before collection, leaves pytest nothing to
re-execute: it only creates namespace stand-ins for the hyphenated
directories. The collection-finish hook turns any recurrence into a hard
error instead of an order-dependent failure elsewhere in the run.
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
