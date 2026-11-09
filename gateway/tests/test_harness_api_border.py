"""``gateway/`` imports the agent harness only through its API.

The API modules are listed once in ``tests/shared/harness_api``; the
allowlist below is compared exactly in both directions, so it can only shrink.
Sources are scanned as AST rather than text so that multi-line imports and
dynamic ``importlib.import_module(...)`` calls are covered.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.shared.harness_api import (
    assert_internal_imports_match_allowlist,
    internal_harness_imports,
    internal_harness_imports_under,
    is_api_module,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Internal harness modules gateway/ still imports directly. Empty: every harness
#: name the gateway uses comes through the API.
_ALLOWED_INTERNAL_IMPORTS: frozenset[str] = frozenset()


def test_gateway_imports_the_harness_only_through_its_api() -> None:
    imported = internal_harness_imports_under(
        REPO_ROOT / "gateway", exclude_parts=frozenset({"tests"})
    )
    assert_internal_imports_match_allowlist(imported, _ALLOWED_INTERNAL_IMPORTS, package="gateway/")


def test_only_the_listed_spi_roles_are_api_modules() -> None:
    """A future module under ``spi/`` is internal until it is added to the role list."""
    assert is_api_module("core.agent_harness.spi.session_goal")
    assert not is_api_module("core.agent_harness.spi.internal_helper")
    assert not is_api_module("core.agent_harness.spi.session_goal.impl")
    assert not is_api_module("core.agent_harness.spi")


def test_a_submodule_imported_from_an_api_package_is_internal() -> None:
    """``from core.agent_harness import harness`` imports an internal module; only exported names are API."""
    tree = ast.parse(
        "from core.agent_harness import harness\n"
        "from core.agent_harness import AgentSession\n"
        "from core.agent_harness.spi.session_goal import SessionGoal\n"
        "from core.agent_harness.spi import session_goal\n"
    )
    assert internal_harness_imports(tree) == {
        "core.agent_harness.harness",
        "core.agent_harness.spi",
    }
