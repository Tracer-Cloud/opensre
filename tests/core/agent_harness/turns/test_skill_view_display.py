"""Loading a skill must not print anything through the generic formatter.

``skill_view`` returns the full recipe so the *model* can follow it. The
user-facing event ("Skill <name>" / "↳ Skill activated") is rendered live by
the surface's tool-event observer, so the end-of-turn generic formatter must
stay silent: any output here would double-print the activation, and falling
through to the payload would dump the entire 5k-character skill body —
escaped JSON, box-drawing characters and all — onto the user's screen.
"""

from __future__ import annotations

from core.agent_harness.turns.display_text import format_generic_tool_payload
from core.llm.types import ToolCall
from tools.interactive_shell.actions.skill_view import execute_skill_view_tool


class _NoContext:
    pass


def _skill_view_result() -> dict[str, object]:
    return execute_skill_view_tool({"name": "delivering-morning-briefings"}, _NoContext())


def test_loading_a_skill_emits_nothing_from_the_generic_formatter() -> None:
    """The observer owns the activation event; the formatter must not duplicate it."""
    # Arrange
    import json

    result = _skill_view_result()
    assert result["ok"] is True, result

    class _Result:
        content = json.dumps(result)
        details = result

    # Act
    shown = format_generic_tool_payload(
        ToolCall(id="t1", name="skill_view", input={"name": "delivering-morning-briefings"}),
        _Result(),
    )

    # Assert
    assert shown == "", f"expected silence, got {len(shown)} chars"
