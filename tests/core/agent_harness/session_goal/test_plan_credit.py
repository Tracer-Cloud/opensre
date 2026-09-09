"""The goal checklist is credited from completed plan steps, and a stalled goal pauses."""

from __future__ import annotations

from typing import Any

from core.agent_harness.session_goal.goal import SessionGoal
from core.agent_harness.session_goal.plan_credit import credit_completed_plan_steps
from core.agent_harness.session_goal.run_until import (
    STALL_OPTION_MORE,
    STALL_OPTION_STOP,
    goal_has_stalled,
    pause_for_no_progress,
)
from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan


def _goal(
    *items: str,
    turns_used: int = 0,
    completed: frozenset[int] = frozenset(),
    last_progress_turns_used: int = 0,
) -> SessionGoal:
    return SessionGoal(
        condition="Report failing CI checks, then set up a recurring check",
        checklist=tuple(items),
        completed=completed,
        turns_used=turns_used,
        last_progress_turns_used=last_progress_turns_used,
        max_outer_turns=6,
    )


def _session_with_plan(*steps: tuple[str, PlanStepStatus]) -> Any:
    plan = TaskPlan(steps=tuple(PlanStep(step=text, status=status) for text, status in steps))
    return type("Session", (), {"task_plan": plan})()


def test_completed_plan_steps_tick_the_matching_checklist_items() -> None:
    # Arrange: the plan lists the checklist's steps, two of them done.
    goal = _goal(
        "Summarize failing PR checks with links",
        "Create an interactive repair handoff",
        "Set a weekday 8:00 AM read-only recurring check",
    )
    session = _session_with_plan(
        ("Summarize failing PR checks with links", PlanStepStatus.COMPLETED),
        ("Create an interactive repair handoff", PlanStepStatus.COMPLETED),
        ("Set weekday 8:00 AM recurring check", PlanStepStatus.IN_PROGRESS),
    )

    # Act
    credited = credit_completed_plan_steps(goal, session)

    # Assert: exact and near matches count, the unfinished step does not.
    assert credited.completed == frozenset({0, 1})


def test_goal_without_a_plan_or_checklist_is_untouched() -> None:
    goal = _goal("Summarize failing PR checks with links")
    assert credit_completed_plan_steps(goal, type("S", (), {})()) is goal
    assert credit_completed_plan_steps(_goal(), _session_with_plan()) == _goal()


def test_goal_stalls_after_two_turns_without_a_new_tick() -> None:
    assert goal_has_stalled(_goal("a", "b", turns_used=2)) is True
    assert goal_has_stalled(_goal("a", "b", turns_used=1)) is False
    assert (
        goal_has_stalled(
            _goal(
                "a",
                "b",
                turns_used=3,
                completed=frozenset({0}),
                last_progress_turns_used=1,
            )
        )
        is True
    )
    assert (
        goal_has_stalled(
            _goal(
                "a",
                "b",
                turns_used=3,
                completed=frozenset({0}),
                last_progress_turns_used=3,
            )
        )
        is False
    )
    assert goal_has_stalled(_goal(turns_used=4)) is True
    assert goal_has_stalled(_goal(turns_used=4, last_progress_turns_used=4)) is False


def test_a_new_tick_records_the_turn_so_later_plateaus_can_stall() -> None:
    progressed = _goal("a", "b", turns_used=1).with_completed(frozenset({0}))
    assert progressed.last_progress_turns_used == 1
    assert goal_has_stalled(progressed) is False


def test_a_stalled_goal_pauses_and_offers_the_ways_forward_as_a_menu() -> None:
    # Arrange: a shell-like session that records what the pause queues.
    from surfaces.interactive_shell.session import Session

    session = Session()
    painted: list[SessionGoal] = []

    # Act
    paused = pause_for_no_progress(session, _goal("a", "b", turns_used=2), painted.append)

    # Assert: paused with a plain reason, menu queued, its options run goal commands.
    assert paused.status == "paused"
    assert "no progress" in paused.last_reason
    assert painted and painted[-1].status == "paused"
    choice = session.pending_user_choice
    assert choice is not None
    assert choice.options == (STALL_OPTION_MORE, STALL_OPTION_STOP)
    assert choice.commands == {STALL_OPTION_MORE: "/goal resume", STALL_OPTION_STOP: "/goal clear"}
    assert session.terminal.pending_prompt_default == "/choose"


def test_headless_stall_pauses_without_a_choose_menu() -> None:
    from core.agent_harness.session import InMemorySessionStore, SessionCore

    session = SessionCore(store=InMemorySessionStore())
    painted: list[SessionGoal] = []

    paused = pause_for_no_progress(session, _goal("a", "b", turns_used=2), painted.append)

    assert paused.status == "paused"
    assert painted and painted[-1].status == "paused"
    assert session.pending_user_choice is None


def test_replacing_or_clearing_a_goal_drops_the_prior_plan() -> None:
    from core.agent_harness.session import InMemorySessionStore, SessionCore
    from core.agent_harness.session_goal.goal import attach_session_goal, clear_session_goal

    session = SessionCore(store=InMemorySessionStore())
    first = attach_session_goal(session, _goal("Summarize failing PR checks with links"))
    leftover = TaskPlan(
        steps=(
            PlanStep(
                step="Summarize failing PR checks with links",
                status=PlanStepStatus.COMPLETED,
            ),
        )
    )
    session.task_plan = leftover
    attach_session_goal(session, first)
    assert session.task_plan is leftover

    clear_session_goal(session)
    assert session.task_plan is None

    session.task_plan = leftover
    attach_session_goal(session, _goal("Summarize failing PR checks with links"))
    assert session.task_plan is None
    assert credit_completed_plan_steps(session.session_goal, session).completed == frozenset()
