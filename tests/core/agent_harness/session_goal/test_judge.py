"""Cheap-model transcript judge: met / not yet / impossible + evidence gate."""

from __future__ import annotations

from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.evaluate import evaluate_session_goal
from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    attach_session_goal,
)
from core.agent_harness.session_goal.judge import SessionGoalJudgeVerdict
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.llm.types import AgentLLMResponse


def _result(text: str, *, executed: int = 0, success: int = 0) -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=executed,
            executed_count=executed,
            executed_success_count=success,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text=text,
    )


def _reached(**_kw: object) -> SessionGoalJudgeVerdict:
    return SessionGoalJudgeVerdict(
        verdict="GOAL_REACHED",
        reason="272 Windows users in the last 7 days",
    )


def _not_yet(**_kw: object) -> SessionGoalJudgeVerdict:
    return SessionGoalJudgeVerdict(
        verdict="NOT_REACHED",
        reason="use the Actions runs endpoint by SHA",
    )


def _impossible(**_kw: object) -> SessionGoalJudgeVerdict:
    return SessionGoalJudgeVerdict(
        verdict="IMPOSSIBLE",
        reason="signup identity is unverified in this project",
    )


def test_tools_plus_real_answer_meets_without_a_tag() -> None:
    session = SessionCore()
    attach_session_goal(
        session,
        SessionGoal(
            condition="How many Windows users in the last 7 days?",
            max_outer_turns=4,
            host_owned=True,
        ),
    )
    verdict = evaluate_session_goal(
        session.session_goal,  # type: ignore[arg-type]
        _result("I found 272 Windows users.", executed=2, success=2),
        session=session,
        judge=_reached,
    )
    assert verdict.status == SessionGoalStatus.ACHIEVED
    assert "272" in verdict.reason
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.ACHIEVED


def test_chatty_reply_without_tools_stays_not_yet() -> None:
    session = SessionCore()
    attach_session_goal(
        session,
        SessionGoal(condition="how many failed Actions runs?", max_outer_turns=4),
    )
    verdict = evaluate_session_goal(
        session.session_goal,  # type: ignore[arg-type]
        _result("Looks done from history."),
        session=session,
        judge=_reached,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert SessionGoalReason.NEED_TOOL_EVIDENCE in verdict.reason


def test_judge_not_yet_reason_is_the_status_reason() -> None:
    verdict = evaluate_session_goal(
        SessionGoal(condition="find the failing run", max_outer_turns=4),
        _result("I listed runs on main.", executed=1, success=1),
        judge=_not_yet,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason == "use the Actions runs endpoint by SHA"


def test_impossible_is_terminal() -> None:
    session = SessionCore()
    attach_session_goal(
        session,
        SessionGoal(condition="D7 retention for Windows signups", max_outer_turns=4),
    )
    verdict = evaluate_session_goal(
        session.session_goal,  # type: ignore[arg-type]
        _result("signup event unverified"),
        session=session,
        judge=_impossible,
    )
    assert verdict.status == SessionGoalStatus.IMPOSSIBLE
    assert "unverified" in verdict.reason
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.IMPOSSIBLE


def test_transport_failure_stays_active() -> None:
    class _Boom:
        model_id = "test"

        def invoke(self, messages, *, system=None, tools=None):  # noqa: ANN001
            _ = (messages, system, tools)
            raise RuntimeError("classifier down")

        def with_structured_output(self, model):  # noqa: ANN001
            _ = model
            raise RuntimeError("classifier down")

    session = SessionCore()
    goal = SessionGoal(condition="finish migration", max_outer_turns=3)
    attach_session_goal(session, goal)
    verdict = evaluate_session_goal(
        goal,
        _result("still working", executed=1, success=1),
        session=session,
        judge_llm=_Boom(),  # type: ignore[arg-type]
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason == SessionGoalReason.JUDGE_UNAVAILABLE
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.ACTIVE


def test_structured_llm_not_reached_does_not_false_complete() -> None:
    class _LLM:
        model_id = "test"

        def invoke(self, messages, *, system=None, tools=None):  # noqa: ANN001
            _ = (messages, system, tools)
            return AgentLLMResponse(
                content='{"verdict": "NOT_REACHED", "reason": "still missing the SHA filter"}'
            )

        def tool_schemas(self, tools):  # noqa: ANN001
            _ = tools
            return []

    status = evaluate_session_goal(
        SessionGoal(condition="find the failing run", max_outer_turns=3),
        _result("listed runs", executed=1, success=1),
        judge_llm=_LLM(),  # type: ignore[arg-type]
    )
    assert status.status == SessionGoalStatus.ACTIVE
    assert "SHA" in status.reason
