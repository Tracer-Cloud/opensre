"""``surfaces/`` imports the agent harness only through its API.

Twin of ``gateway/tests/test_harness_api_border.py``. The allowlist is compared
exactly in both directions, so it can only shrink: a new internal import fails
immediately, and an entry no longer imported must be removed.
"""

from __future__ import annotations

from pathlib import Path

from tests.shared.harness_api import (
    assert_internal_imports_match_allowlist,
    internal_harness_imports_under,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Internal harness modules surfaces/ still imports directly. Empty: every
#: harness name surfaces/ uses comes through the API.
_ALLOWED_INTERNAL_IMPORTS: frozenset[str] = frozenset()


def test_surfaces_import_the_harness_only_through_its_api() -> None:
    imported = internal_harness_imports_under(REPO_ROOT / "surfaces")
    assert_internal_imports_match_allowlist(
        imported, _ALLOWED_INTERNAL_IMPORTS, package="surfaces/"
    )


#: Interactive shell modules allowed to call unguarded chat() / chat_until_goal() directly.
#: Empty: the interactive shell must drive turns via TurnRunner -> SessionAgentPool -> HeadlessAgent.handle.
#: Compared exactly, so it can only shrink.
_ALLOWED_SHELL_CHAT_CALLERS: frozenset[str] = frozenset()


def _shell_chat_callers() -> dict[str, set[str]]:
    """Interactive shell modules calling .chat() or .chat_until_goal()."""
    import ast

    shell_root = REPO_ROOT / "surfaces" / "interactive_shell"
    callers: dict[str, set[str]] = {}
    for path in sorted(shell_root.rglob("*.py")):
        if {"__pycache__", "tests"} & set(path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        methods = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"chat", "chat_until_goal"}
        }
        if methods:
            callers[str(path.relative_to(REPO_ROOT))] = methods
    return callers


def test_interactive_shell_never_calls_unguarded_chat() -> None:
    """Interactive shell turns must go through TurnRunner -> HeadlessAgent.handle, never chat().

    AgentSession.chat() and chat_until_goal() bypass the session agent pool lock
    and process turn capacity gate. Interactive shell modules must not call them.
    """
    callers = _shell_chat_callers()
    actual = set(callers.keys())

    added = sorted(actual - _ALLOWED_SHELL_CHAT_CALLERS)
    assert added == [], (
        f"interactive shell modules calling unguarded chat() / chat_until_goal(): {callers}. "
        "Route turns through infrastructure/turn_host/turn_runner.py (TurnRunner.run / "
        "HeadlessAgent.handle) to preserve turn serialization and capacity limits."
    )

    stale = sorted(_ALLOWED_SHELL_CHAT_CALLERS - actual)
    assert stale == [], (
        f"{stale} no longer call chat(); remove them from the allowlist so it keeps shrinking."
    )
