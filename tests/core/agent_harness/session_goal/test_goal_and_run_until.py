"""SessionGoal: explicit attach and cross-turn continuation."""

from __future__ import annotations

from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.evaluate import evaluate_session_goal
from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalStatus,
    attach_session_goal,
    build_session_goal,
    derive_session_goal_checklist,
    session_goal_is_active,
)
from core.agent_harness.session_goal.judge import SessionGoalJudgeVerdict
from core.agent_harness.session_goal.run_until import run_until_session_goal
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


def _keep_ticks(**kw: object) -> frozenset[int]:
    newly = kw.get("newly")
    return newly if isinstance(newly, frozenset) else frozenset()


_FIVE_STEP_ASK = (
    "Do this 5-step sequential process without asking whether to continue: "
    "(1) list the goal, (2) name step one, (3) name step two, "
    "(4) name step three, (5) confirm all five are done."
)


def test_a_checklist_comes_from_numbered_steps_or_explicit_items_only() -> None:
    # A single item would only repeat the condition, so a plain condition gets none.
    assert derive_session_goal_checklist("How many Windows users?") == ()
    # Inline numbering in any of the common spellings, in order, is a checklist.
    assert derive_session_goal_checklist("1. list PRs, 2. check runs, 3. make a table") == (
        "list PRs",
        "check runs",
        "make a table",
    )
    assert derive_session_goal_checklist("(1) list PRs (2) check runs") == (
        "list PRs",
        "check runs",
    )
    # Numbers that are not a 1, 2, 3 sequence are prose, not steps.
    assert derive_session_goal_checklist("compare v2. 0 with 3. 1 quickly") == ()
    # An inline ``1.`` mid-sentence is prose; after a colon it opens a list.
    assert derive_session_goal_checklist("fix the build then 1. rerun 2. report") == ()
    assert derive_session_goal_checklist("Do this: 1. rerun the job 2. report the result") == (
        "rerun the job",
        "report the result",
    )
    assert derive_session_goal_checklist("Steps;1) rerun 2) report") == ("rerun", "report")
    # Parenthesised markers are unambiguous anywhere.
    assert derive_session_goal_checklist("please (1) rerun the job and (2) report") == (
        "rerun the job and",
        "report",
    )
    assert derive_session_goal_checklist(
        "Do this:\n1. list the goal\n2. name step one\n3. confirm done"
    ) == ("list the goal", "name step one", "confirm done")
    assert derive_session_goal_checklist("ignored", ("A", "B")) == ("A", "B")


def test_build_session_goal_from_structured_input() -> None:
    goal = build_session_goal(
        condition=_FIVE_STEP_ASK,
        checklist=("one", "two", "three", "four", "five"),
        max_outer_turns=5,
    )
    assert goal.max_outer_turns == 5
    assert goal.step_count == 5
    assert goal.status == SessionGoalStatus.ACTIVE


def test_attach_session_goal_on_session_core() -> None:
    session = SessionCore()
    goal = SessionGoal(condition="finish the checklist", max_outer_turns=3)
    attached = attach_session_goal(session, goal)
    assert session.session_goal is attached
    assert attached.condition == goal.condition
    assert attached.started_at is not None
    assert session_goal_is_active(session) is True


def test_clear_session_clears_session_goal() -> None:
    session = SessionCore()
    attach_session_goal(session, SessionGoal(condition="x", max_outer_turns=2))
    session.clear()
    assert session.session_goal is None
    assert session_goal_is_active(session) is False


def test_five_step_outer_loop_continues_until_achieved() -> None:
    session = SessionCore()
    turns: list[str] = []
    checklist = ("list the goal", "step one", "step two", "step three", "confirm done")

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        stored = session.session_goal
        if isinstance(stored, SessionGoal):
            nxt = len(stored.completed)
            if nxt < len(stored.checklist):
                attach_session_goal(session, stored.with_completed(stored.completed | {nxt}))
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text="Completed item.",
        )

    outcome = run_until_session_goal(
        _chat,
        session,
        _FIVE_STEP_ASK,
        goal=SessionGoal(
            condition="complete all five steps",
            max_outer_turns=5,
            step_count=5,
            checklist=checklist,
        ),
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(
                goal,
                result,
                session=session,
                judge=lambda **_kw: SessionGoalJudgeVerdict(
                    verdict="NOT_REACHED", reason="checklist still open"
                ),
                validate=_keep_ticks,
            ).status
        ),
    )

    assert len(turns) == 5
    assert turns[0] == _FIVE_STEP_ASK
    assert outcome.goal.status == SessionGoalStatus.ACHIEVED
    assert outcome.turn_count == 5
    assert outcome.goal.completed == frozenset({0, 1, 2, 3, 4})
    # Progress tags are stripped before the user-visible reply is returned.
    assert "session_goal:" not in (outcome.last_result.assistant_response_text or "")


def test_outer_loop_disabled_fails_five_step_probe() -> None:
    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text=f"Completed step {len(turns)} of 5.",
        )

    outcome = run_until_session_goal(
        _chat,
        session,
        _FIVE_STEP_ASK,
        goal=SessionGoal(
            condition="complete all five steps",
            max_outer_turns=1,
        ),
        evaluate=lambda *_a, **_k: SessionGoalStatus.ACTIVE,
    )

    assert len(turns) == 1
    assert outcome.goal.status == SessionGoalStatus.BUDGET_EXHAUSTED


def test_without_goal_outer_loop_is_single_chat() -> None:
    """No explicit goal means one turn; user prose is not auto-detected."""
    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text="ok",
        )

    outcome = run_until_session_goal(_chat, session, _FIVE_STEP_ASK)

    assert len(turns) == 1
    assert outcome.turn_count == 1
    assert outcome.goal.status == SessionGoalStatus.CLEARED


def test_paused_goal_outer_loop_is_single_chat_without_turn_bump() -> None:
    """``/goal pause`` keeps state; host must not continue or spend budget."""
    session = SessionCore()
    attach_session_goal(
        session,
        SessionGoal(
            condition="finish later",
            max_outer_turns=5,
            status=SessionGoalStatus.PAUSED,
            turns_used=2,
            host_owned=True,
        ),
    )
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text="side question answered",
        )

    outcome = run_until_session_goal(_chat, session, "unrelated question")

    assert len(turns) == 1
    assert turns[0] == "unrelated question"
    assert outcome.goal.status == SessionGoalStatus.PAUSED
    assert outcome.goal.turns_used == 2
    assert outcome.turn_count == 2
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.PAUSED


def test_the_judge_reason_is_painted_between_turns() -> None:
    # Arrange: a two-turn goal whose judge says "not yet" with a concrete next step.
    from core.agent_harness.session_goal.evaluate import evaluate_session_goal
    from core.agent_harness.session_goal.judge import SessionGoalJudgeVerdict

    session = SessionCore()
    painted: list[str] = []

    def _chat(message: str) -> TurnResult:
        _ = message
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text="listed runs on main",
        )

    def _not_yet(**_kw: object) -> SessionGoalJudgeVerdict:
        return SessionGoalJudgeVerdict(verdict="NOT_REACHED", reason="filter runs by head_sha")

    # Act
    run_until_session_goal(
        _chat,
        session,
        "go",
        goal=SessionGoal(condition="find the failing run", max_outer_turns=2),
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(goal, result, session=session, judge=_not_yet).status
        ),
        on_progress=lambda goal: painted.append(goal.last_reason),
    )

    # Assert: the reason shows after turn 1, before the turn-2 working line.
    assert "filter runs by head_sha" in painted
    assert painted.index("filter runs by head_sha") < painted.index(
        "working — starting session-goal turn 2/2"
    )


def test_a_resumed_goal_counts_its_next_turn() -> None:
    # Arrange: a goal already two turns in (the stall menu's "keep going" path).
    from core.agent_harness.session_goal.evaluate import evaluate_session_goal
    from core.agent_harness.session_goal.judge import SessionGoalJudgeVerdict

    session = SessionCore()
    attach_session_goal(
        session,
        SessionGoal(condition="find the failing run", max_outer_turns=5, turns_used=2),
    )

    def _chat(message: str) -> TurnResult:
        _ = message
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text="listed runs",
        )

    # Act: the resumed condition runs as one more turn; the judge is not satisfied.
    outcome = run_until_session_goal(
        _chat,
        session,
        "find the failing run",
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(
                goal,
                result,
                session=session,
                judge=lambda **_kw: SessionGoalJudgeVerdict(
                    verdict="NOT_REACHED", reason="still looking"
                ),
            ).status
        ),
    )

    # Assert: the turn counter moved past 2, so a stall check and the budget see it.
    assert outcome.turn_count >= 3
