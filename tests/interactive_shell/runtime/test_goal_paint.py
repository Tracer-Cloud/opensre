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


def test_working_status_is_one_line_with_real_elapsed() -> None:
    from core.agent_harness.session_goal.goal import SessionGoalReason, mark_session_goal_started

    session = Session()
    goal = mark_session_goal_started(
        _goal(last_reason=SessionGoalReason.working_session_turn(2, 6)),
        now=1_000.0,
        input_tokens=10,
        output_tokens=2,
    )
    painted = goal_paint_text(goal, session)
    assert painted.count("\n") == 0
    assert "working — starting session-goal turn 2/6" in painted
    assert "Checklist:" not in painted


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


def test_a_new_judge_reason_paints_the_full_block_again() -> None:
    session = Session()
    goal_paint_text(_goal(last_reason="not yet — list the failing SHA"), session)

    painted = goal_paint_text(
        _goal(last_reason="not yet — filter workflow runs by head_sha"), session
    )

    assert "Checklist:" in painted
    assert "head_sha" in painted
