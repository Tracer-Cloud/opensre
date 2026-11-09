"""``tools/``, ``integrations/`` and ``infrastructure/`` import the agent harness only through its API.

Third border test after the shell and gateway ones. ``bootstrap/`` is exempt: it
is the composition root and wiring harness internals is its role.

The allowlist is compared exactly in both directions, so it can only shrink: a
new internal import fails immediately, and an entry no longer imported must be
removed.
"""

from __future__ import annotations

from pathlib import Path

from tests.shared.harness_api import (
    assert_internal_imports_match_allowlist,
    internal_harness_imports_under,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Internal harness modules the tool tier still imports directly. Empty: every
#: harness name the tool tier uses comes through the API.
_ALLOWED_INTERNAL_IMPORTS: frozenset[str] = frozenset()


def test_tool_tier_imports_the_harness_only_through_its_api() -> None:
    imported = internal_harness_imports_under(
        REPO_ROOT / "tools", REPO_ROOT / "integrations", REPO_ROOT / "infrastructure"
    )
    assert_internal_imports_match_allowlist(
        imported, _ALLOWED_INTERNAL_IMPORTS, package="tools/, integrations/, infrastructure/"
    )
