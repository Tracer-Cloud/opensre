"""Goal edits reconcile persisted progress without lifting execution restrictions."""

from io import StringIO
from typing import Any

import pytest
from rich.console import Console

from core.agent_harness.session import InMemorySessionStore, SessionManager
from core.agent_harness.session_goal.evaluate import evaluate_session_goal
from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalStatus,
    attach_session_goal,
    build_session_goal,
)
from core.agent_harness.session_goal.judge import SessionGoalJudgeVerdict
from core.agent_harness.task_plan import PlanStep, PlanStepStatus, TaskPlan
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from surfaces.interactive_shell.command_registry.session_cmds.goal import _cmd_goal
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.shared.execution_policy import allow_tool, apply_plan_only_gate


def _session() -> Session:
    session = Session()
    session.store = InMemorySessionStore()
    session.store.open_session(session)
    session.store.append_turn(session, "chat", "goal edit regression")
    return session


def _edit_and_restore(session: Session, condition: str) -> Session:
    assert _cmd_goal(session, Console(file=StringIO()), ["edit", condition])
    assert isinstance(session.store, InMemorySessionStore)
    data: dict[str, Any] = {}
    for record in session.store.read(session.session_id):
        if record.get("type") == "custom_message":
            data[str(record.get("custom_type"))] = record.get("content")
    restored = _session()
    return SessionManager.for_session(restored).restore_context(restored, data)


def test_edit_and_restore_remaps_progress_once_per_duplicate() -> None:
    session = _session()
    attach_session_goal(
        session,
        SessionGoal(
            condition="1. Check API 2. Check API 3. Check database",
            checklist=("Check API", "Check API", "Check database"),
            completed=frozenset({0, 2}),
            step_count=3,
            turns_used=2,
            max_outer_turns=8,
            status=SessionGoalStatus.PAUSED,
        ),
    )
    restored = _edit_and_restore(
        session, "1. Check database 2. Check API 3. Check API 4. Check queue"
    )
    goal = restored.session_goal
    assert goal is not None
    assert goal.checklist == ("Check database", "Check API", "Check API", "Check queue")
    assert goal.completed == frozenset({0, 1})
    assert goal.step_count == 4
    assert goal.turns_used == 2 and goal.max_outer_turns == 8
    assert goal.status == SessionGoalStatus.PAUSED


@pytest.mark.parametrize("condition", ["Investigate latency", "1. Inspect API 2. Inspect database"])
def test_edit_and_restore_preserves_explicit_items_for_prose_condition(condition: str) -> None:
    session = _session()
    goal = build_session_goal(condition, checklist=("Check API", "Check database"))
    attach_session_goal(session, goal.with_completed(frozenset({0})))
    session = _edit_and_restore(session, condition)
    restored = _edit_and_restore(session, "Investigate latency in production")
    goal = restored.session_goal
    assert goal is not None
    assert goal.checklist == ("Check API", "Check database")
    assert goal.completed == frozenset({0})
    assert goal.step_count == 2
    assert goal.checklist_explicit is True
    assert session.terminal.pending_prompt_default == goal.condition


@pytest.mark.parametrize("remove_steps", [False, True])
def test_edit_repairs_a_restored_legacy_checklist(remove_steps: bool) -> None:
    session = _session()
    condition = "1. Check API 2. Check database 3. Check queue"
    SessionManager.for_session(session).restore_context(
        session,
        {
            "session_goal_state": {
                "session_goal": {
                    "condition": condition,
                    "checklist": ["Check API", "Check database"],
                    "step_count": 2,
                    "completed": [0],
                    "status": "paused",
                }
            }
        },
    )
    restored = _edit_and_restore(session, "Investigate latency" if remove_steps else condition)
    goal = restored.session_goal
    assert goal is not None
    assert goal.checklist == (
        () if remove_steps else ("Check API", "Check database", "Check queue")
    )
    assert goal.step_count == (None if remove_steps else 3)
    assert goal.completed == (frozenset() if remove_steps else frozenset({0}))


def test_edit_and_restore_keeps_execution_restricted_after_discarding_plan() -> None:
    session = _session()
    attach_session_goal(session, build_session_goal("1. Check API 2. Check database"))
    session.task_plan = TaskPlan((PlanStep("Check database", PlanStepStatus.IN_PROGRESS),))
    session.plan_only_until_authorized = True
    restored = _edit_and_restore(session, "1. Check API 2. Check queue")
    for current in (session, restored):
        assert current.task_plan is None
        assert current.plan_only_until_authorized is True
        verdict = apply_plan_only_gate(
            allow_tool("shell"), plan_only_active=current.plan_only_until_authorized
        )
        assert verdict.verdict == "ask"


@pytest.mark.parametrize("old_evidence", [("old tool result",), None])
def test_edit_and_restore_resets_prior_tool_evidence(
    old_evidence: tuple[str, ...] | None,
) -> None:
    session = _session()
    attach_session_goal(
        session,
        SessionGoal(
            condition="Investigate API latency",
            tool_evidence=old_evidence,
            tool_success_seen=True,
        ),
    )
    restored = _edit_and_restore(session, "Investigate database latency")
    goal = restored.session_goal
    assert goal is not None
    assert goal.tool_evidence == ()
    assert goal.tool_success_seen is False

    def _reached(**_kwargs: object) -> SessionGoalJudgeVerdict:
        return SessionGoalJudgeVerdict(verdict="GOAL_REACHED", reason="investigated")

    no_tool = TurnResult("cli_agent_handled", ToolCallingTurnResult(0, 0, 0, False, True), "Done")
    assert evaluate_session_goal(goal, no_tool, judge=_reached).status == SessionGoalStatus.ACTIVE

    new_tool = TurnResult("cli_agent_handled", ToolCallingTurnResult(1, 1, 1, False, True), "Done")
    assert (
        evaluate_session_goal(goal, new_tool, judge=_reached).status == SessionGoalStatus.ACHIEVED
    )
