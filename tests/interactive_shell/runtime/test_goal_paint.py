"""An unchanged session goal repaints as one line, not the whole block again."""

from __future__ import annotations

from core.agent_harness.session_goal.goal import SessionGoal
from surfaces.interactive_shell.runtime.shell_turn_execution import goal_paint_text
from surfaces.interactive_shell.session import Session


def _goal(**overrides: object) -> SessionGoal:
    base: dict[str, object] = {
        "condition": "Report failing CI checks, then set up a recurring check",
        "checklist": ("Summarize failing PR checks", "Set a recurring check"),
        "max_outer_turns": 6,
        "turns_used": 1,
        "status": "active",
    }
    base.update(overrides)
    return SessionGoal(**base)  # type: ignore[arg-type]


def test_same_goal_paints_the_block_once_then_one_status_line() -> None:
    session = Session()
    first = goal_paint_text(_goal(), session)
    second = goal_paint_text(_goal(turns_used=2), session)

    assert "Checklist:" in first
    assert "Checklist:" not in second
    assert second.count("\n") == 0


def test_progress_or_status_change_paints_the_full_block_again() -> None:
    session = Session()
    goal_paint_text(_goal(), session)

    ticked = goal_paint_text(_goal(turns_used=2, completed=frozenset({0})), session)
    paused = goal_paint_text(
        _goal(turns_used=3, completed=frozenset({0}), status="paused"), session
    )

    assert "[x] 1." in ticked
    assert "Checklist:" in paused


def test_a_new_goal_with_the_same_shape_paints_the_full_block() -> None:
    session = Session()
    goal_paint_text(_goal(started_at=1.0), session)

    painted = goal_paint_text(_goal(started_at=2.0), session)

    assert "Checklist:" in painted
