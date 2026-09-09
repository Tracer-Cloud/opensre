"""SessionGoal: explicit attach and cross-turn continuation."""

from __future__ import annotations

from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.evaluate import evaluate_session_goal
from core.agent_harness.session_goal.goal import (
    SESSION_GOAL_CHECKPOINT_TURNS,
    SessionGoal,
    SessionGoalReason,
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


def test_build_session_goal_is_unbounded_unless_the_caller_sets_a_cap() -> None:
    goal = build_session_goal(condition="count the open PRs")
    assert goal.max_outer_turns == 0


def test_an_unbounded_goal_does_not_stop_on_turn_count() -> None:
    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text="still working",
        )

    outcome = run_until_session_goal(
        _chat,
        session,
        "go",
        goal=SessionGoal(condition="keep going", max_outer_turns=0),
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(
                goal,
                result,
                session=session,
                judge=lambda **_kw: SessionGoalJudgeVerdict(
                    verdict="NOT_REACHED", reason="not yet"
                ),
            ).status
        ),
        cancel_requested=lambda: len(turns) >= 8,
    )
    assert len(turns) == 8
    assert outcome.goal.status != SessionGoalStatus.BUDGET_EXHAUSTED


def test_an_unbounded_goal_pauses_for_a_decision_at_the_checkpoint() -> None:
    """Successful tool activity alone must not let a goal without a budget run on unattended."""
    # Arrange: every turn succeeds with a tool, the judge never says reached.
    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text="still working",
        )

    # Act
    outcome = run_until_session_goal(
        _chat,
        session,
        "go",
        goal=SessionGoal(condition="keep going", max_outer_turns=0),
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(
                goal,
                result,
                session=session,
                judge=lambda **_kw: SessionGoalJudgeVerdict(
                    verdict="NOT_REACHED", reason="not yet"
                ),
            ).status
        ),
        cancel_requested=lambda: len(turns) >= 2 * SESSION_GOAL_CHECKPOINT_TURNS,
    )

    # Assert: stopped at the checkpoint, still active for the next message (headless).
    assert len(turns) == SESSION_GOAL_CHECKPOINT_TURNS
    assert outcome.goal.status == SessionGoalStatus.ACTIVE
    assert outcome.goal.last_reason == SessionGoalReason.WAITING_AFTER_CHECKPOINT


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
                validate=_keep_ticks,
            ).status
        ),
    )

    assert len(turns) == 5
    assert _FIVE_STEP_ASK in turns[0]
    assert "[session_goal]" in turns[0]
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
        goal=SessionGoal(
            condition="find the failing run",
            max_outer_turns=2,
            checklist=("list the runs", "filter by SHA"),
        ),
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
        SessionGoal(
            condition="find the failing run",
            max_outer_turns=5,
            turns_used=2,
            checklist=("list the runs", "filter by SHA"),
        ),
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


def test_a_goal_turn_that_raises_pauses_the_goal_before_the_error_propagates() -> None:
    # Arrange: an active goal whose next turn hits a credit wall.
    import pytest

    from core.agent_harness.session_goal.goal import SessionGoalReason
    from core.llm.shared.llm_retry import LLMCreditExhaustedError

    session = SessionCore()
    attach_session_goal(
        session, SessionGoal(condition="count the open PRs", max_outer_turns=4, turns_used=1)
    )
    painted: list[str] = []

    def _chat(_message: str) -> TurnResult:
        raise LLMCreditExhaustedError("credits exhausted")

    # Act: the host still sees the error.
    with pytest.raises(LLMCreditExhaustedError):
        run_until_session_goal(
            _chat, session, "count the open PRs", on_progress=lambda g: painted.append(g.status)
        )

    # Assert: the goal is paused with the reason, so the next message does not resume into it.
    stored = session.session_goal
    assert stored is not None
    assert stored.status == SessionGoalStatus.PAUSED
    assert stored.last_reason == SessionGoalReason.PAUSED_TURN_FAILED
    assert painted[-1] == SessionGoalStatus.PAUSED


def test_a_goal_turn_the_driver_could_not_run_pauses_the_goal_instead_of_retrying() -> None:
    # Arrange: the action driver swallowed a rejected key and returned a not_run result.
    from core.agent_harness.session_goal.goal import SessionGoalReason

    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                0, 0, 0, True, True, response_text="key rejected", accounting_status="not_run"
            ),
            assistant_response_text="",
        )

    # Act
    outcome = run_until_session_goal(
        _chat,
        session,
        "count the open PRs",
        goal=SessionGoal(condition="count the open PRs", max_outer_turns=4),
    )

    # Assert: one turn, paused with the failure reason, no continuation into the same error.
    assert len(turns) == 1
    assert "count the open PRs" in turns[0]
    assert "[session_goal]" in turns[0]
    assert outcome.goal.status == SessionGoalStatus.PAUSED
    assert outcome.goal.last_reason == SessionGoalReason.PAUSED_TURN_FAILED


def test_one_idle_turn_after_a_tool_does_not_pause_the_goal() -> None:
    # Arrange: tools, then one idle not-yet. Live five-PR runs stalled here
    # and returned a wrong table. Claude keeps going.
    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        if len(turns) == 1:
            return TurnResult(
                final_intent="cli_agent_handled",
                action_result=ToolCallingTurnResult(
                    planned_count=1,
                    executed_count=1,
                    executed_success_count=1,
                    has_unhandled_clause=False,
                    handled=True,
                ),
                assistant_response_text="listed the PRs",
            )
        return _idle_turn("still no table")

    def _same(**kw: object) -> SessionGoalJudgeVerdict:
        previous = str(kw.get("previous_reason", ""))
        return SessionGoalJudgeVerdict(
            verdict="NOT_REACHED",
            reason="need a live Actions query",
            repeats_previous=bool(previous),
        )

    outcome = run_until_session_goal(
        _chat,
        session,
        "go",
        goal=SessionGoal(condition="table with the five PRs", max_outer_turns=3),
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(goal, result, session=session, judge=_same).status
        ),
    )

    assert len(turns) == 3
    assert outcome.goal.status == SessionGoalStatus.BUDGET_EXHAUSTED
    assert session.pending_user_choice is None


def test_a_repeated_verdict_does_not_stop_a_turn_that_used_a_tool() -> None:
    # Arrange: every turn runs a tool; the judge keeps saying contradiction.
    from core.agent_harness.session_goal.evaluate import evaluate_session_goal
    from core.agent_harness.session_goal.judge import SessionGoalJudgeVerdict

    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text="All 5 checked. | 3 rows |",
        )

    def _same(**kw: object) -> SessionGoalJudgeVerdict:
        previous = str(kw.get("previous_reason", ""))
        return SessionGoalJudgeVerdict(
            verdict="NOT_REACHED",
            reason="Contradiction: the sentence says 5 but the table lists 3 rows",
            repeats_previous=bool(previous),
        )

    outcome = run_until_session_goal(
        _chat,
        session,
        "go",
        goal=SessionGoal(condition="table with the false sentence", max_outer_turns=3),
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(goal, result, session=session, judge=_same).status
        ),
    )

    assert len(turns) == 3
    assert outcome.goal.status == SessionGoalStatus.BUDGET_EXHAUSTED
    assert outcome.goal.findings == ()


def test_a_contradicted_reply_is_not_stored_as_an_established_finding() -> None:
    from core.agent_harness.session_goal.evaluate import evaluate_session_goal
    from core.agent_harness.session_goal.judge import SessionGoalJudgeVerdict

    session = SessionCore()

    def _chat(_message: str) -> TurnResult:
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=1,
                executed_count=1,
                executed_success_count=1,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text="All 5 PRs re-ran to green.",
        )

    outcome = run_until_session_goal(
        _chat,
        session,
        "go",
        goal=SessionGoal(condition="which PRs re-ran to green", max_outer_turns=1),
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(
                goal,
                result,
                session=session,
                judge=lambda **_kw: SessionGoalJudgeVerdict(
                    verdict="NOT_REACHED",
                    reason="Contradiction: the sentence says 5 but only one SHA shows attempt 2",
                ),
            ).status
        ),
    )

    assert outcome.goal.findings == ()
    assert "All 5 PRs re-ran to green." in outcome.goal.last_answer


def _stay_active(_goal: SessionGoal, _result: TurnResult, *, session: object | None = None) -> str:
    _ = session
    return SessionGoalStatus.ACTIVE


def _idle_turn(text: str = "still looking") -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text=text,
    )


def test_headless_stall_keeps_the_goal_so_the_next_message_continues() -> None:
    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return _idle_turn()

    first = run_until_session_goal(
        _chat,
        session,
        "find the failing run",
        goal=SessionGoal(
            condition="find the failing run",
            max_outer_turns=6,
            checklist=("list the runs", "filter by SHA"),
        ),
        evaluate=_stay_active,
    )
    assert first.goal.status == SessionGoalStatus.ACTIVE
    assert first.goal.last_reason == SessionGoalReason.WAITING_AFTER_STALL
    assert first.turn_count == 2
    assert session.pending_user_choice is None

    second = run_until_session_goal(
        _chat,
        session,
        "try the SHA filter",
        evaluate=_stay_active,
    )
    assert any("try the SHA filter" in turn for turn in turns)
    assert second.goal.turns_used >= 3
    assert second.goal.status == SessionGoalStatus.ACTIVE


def test_headless_same_verdict_after_a_tool_keeps_going() -> None:
    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        if len(turns) == 1:
            return TurnResult(
                final_intent="cli_agent_handled",
                action_result=ToolCallingTurnResult(
                    planned_count=1,
                    executed_count=1,
                    executed_success_count=1,
                    has_unhandled_clause=False,
                    handled=True,
                ),
                assistant_response_text="listed the PRs",
            )
        return _idle_turn("still no table")

    def _same(**kw: object) -> SessionGoalJudgeVerdict:
        previous = str(kw.get("previous_reason", ""))
        return SessionGoalJudgeVerdict(
            verdict="NOT_REACHED",
            reason="need a live Actions query",
            repeats_previous=bool(previous),
        )

    outcome = run_until_session_goal(
        _chat,
        session,
        "go",
        goal=SessionGoal(condition="table with the five PRs", max_outer_turns=3),
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(goal, result, session=session, judge=_same).status
        ),
    )
    assert len(turns) == 3
    assert outcome.goal.status == SessionGoalStatus.BUDGET_EXHAUSTED
    assert session.pending_user_choice is None
