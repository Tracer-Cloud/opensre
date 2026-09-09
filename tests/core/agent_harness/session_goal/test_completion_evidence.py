"""Completion must survive unavailable validation and misleading bookkeeping."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from core.agent import AgentRunResult
from core.agent_harness.runtime import ActionTurnRunner
from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.evaluate import (
    build_session_goal_evaluator,
    evaluate_session_goal,
)
from core.agent_harness.session_goal.goal import SessionGoal, SessionGoalStatus, attach_session_goal
from core.agent_harness.session_goal.judge import SessionGoalJudgeVerdict
from core.agent_harness.session_goal.persist import (
    session_goal_from_payload,
    session_goal_to_payload,
)
from core.agent_harness.session_goal.review_input import retain_tool_evidence
from core.agent_harness.session_goal.run_until import run_until_session_goal
from core.agent_harness.turns.headless_adapters import BufferOutputSink, NullToolProvider
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.llm.types import AgentLLMResponse, SchemaDescribedTool, ToolCall
from core.tool import ToolExecutionResult


class _ScriptedLLM:
    model_id = "test"

    def __init__(self, responses: list[AgentLLMResponse]) -> None:
        self.responses = responses
        self.invocations = 0

    def tool_schemas(self, tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
        return [{"name": tool.name} for tool in tools]

    def invoke(self, messages: list[dict[str, Any]], **_kwargs: Any) -> AgentLLMResponse:
        _ = messages
        self.invocations += 1
        return self.responses.pop(0)


def _result(successes: int = 0) -> TurnResult:
    return TurnResult(
        "cli_agent_handled",
        ToolCallingTurnResult(successes, successes, successes, False, True),
        "Claimed done.",
    )


@pytest.mark.parametrize("bookkeeping_turn", [1, 2])
def test_bookkeeping_never_becomes_a_finding(bookkeeping_turn: int) -> None:
    session = SessionCore()
    turns = 0

    def chat(_message: str) -> TurnResult:
        nonlocal turns
        turns += 1
        if turns == bookkeeping_turn:
            assert session.session_goal is not None
            attach_session_goal(
                session, session.session_goal.with_completed(frozenset({0})).with_bookkeeping_call()
            )
            return _result(1)
        return _result()

    def evaluate(goal: SessionGoal, result: TurnResult, **kwargs: Any) -> str:
        return evaluate_session_goal(
            goal,
            result,
            **kwargs,
            judge=lambda **_kw: SessionGoalJudgeVerdict(verdict="GOAL_REACHED"),
            validate=lambda **_kw: frozenset(),
        ).status

    outcome = run_until_session_goal(
        chat,
        session,
        "Deploy",
        goal=SessionGoal(condition="Deploy", checklist=("Deploy",)),
        evaluate=evaluate,
    )
    assert outcome.goal.status != SessionGoalStatus.ACHIEVED
    assert outcome.goal.findings == ()


def test_missing_judge_client_cannot_fall_back_to_unvalidated_checklist() -> None:
    def unavailable() -> Any:
        raise RuntimeError("no classifier")

    session = SessionCore()
    goal = SessionGoal(condition="Deploy", checklist=("Deploy",)).with_completed(frozenset({0}))
    attach_session_goal(session, goal)
    evaluate = build_session_goal_evaluator(unavailable)
    for _ in range(2):
        assert session.session_goal is not None
        assert (
            evaluate(session.session_goal, _result(1), session=session) == SessionGoalStatus.ACTIVE
        )
    assert session.session_goal is not None
    assert session.session_goal.completed == frozenset()


def test_actual_results_and_full_reply_reach_both_reviewers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SessionCore()
    goal = SessionGoal(condition="Deploy prod", checklist=("Deploy",)).with_completed(
        frozenset({0})
    )
    attach_session_goal(session, goal)
    records = [
        (
            ToolCall(id="read", name="read_status", input={"environment": "prod"}),
            ToolExecutionResult(content="old version healthy"),
        ),
        (
            ToolCall(id="deploy", name="deploy_service", input={"environment": "prod"}),
            ToolExecutionResult(content="rollout failed", is_error=True),
        ),
        (
            ToolCall(id="tick", name="session_goal_complete", input={"items": [0]}),
            ToolExecutionResult(content="bookkeeping only"),
        ),
    ]
    reply = "Claimed success. " + "x" * 4100 + " CONTRADICTORY_REPLY_TAIL"

    def run_agent(*_args: Any, **_kwargs: Any) -> AgentRunResult:
        return AgentRunResult(
            messages=[],
            final_text=reply,
            executed=[(call, result.compat_payload()) for call, result in records],
            tool_results=records,
        )

    monkeypatch.setattr(
        "core.agent_harness.turns.action_driver.run_react_agent_with_telemetry", run_agent
    )
    action = ActionTurnRunner(
        output=BufferOutputSink(), tools=NullToolProvider(), llm_factory=lambda: _ScriptedLLM([])
    ).run("Deploy prod", session)
    assert action.evidence_success_count == 1

    class Reviewer(_ScriptedLLM):
        def invoke(self, messages: list[dict[str, Any]], **_kwargs: Any) -> AgentLLMResponse:
            prompt = str(messages)
            assert "read_status" in prompt and "prod" in prompt
            assert "Outcome: error" in prompt and "rollout failed" in prompt
            assert "CONTRADICTORY_REPLY_TAIL" in prompt
            assert "bookkeeping only" not in prompt
            return super().invoke(messages)

    # Even a validator-approved checklist must not bypass the whole-goal judge.
    validator = Reviewer([AgentLLMResponse(content='{"items":[{"index":0,"verdict":"VALID"}]}')])
    judge = Reviewer([AgentLLMResponse(content='{"verdict":"NOT_REACHED"}')])
    verdict = evaluate_session_goal(
        goal,
        TurnResult("cli_agent_handled", action, reply),
        session=session,
        validate_llm=validator,
        judge_llm=judge,
    )
    assert validator.invocations == judge.invocations == 1
    assert verdict.status == SessionGoalStatus.ACTIVE


def test_oversized_evidence_is_not_silently_truncated() -> None:
    session = SessionCore()
    goal = SessionGoal(condition="Deploy", checklist=("Deploy",)).with_completed(frozenset({0}))
    attach_session_goal(session, goal)
    action = ToolCallingTurnResult(1, 1, 1, False, True, tool_evidence="x" * 64000 + "FAILED")
    reviewer = _ScriptedLLM([AgentLLMResponse(content='{"verdict":"GOAL_REACHED"}')])
    verdict = evaluate_session_goal(
        goal,
        TurnResult("cli_agent_handled", action, "Done"),
        session=session,
        judge_llm=reviewer,
        validate_llm=reviewer,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert session.session_goal is not None
    assert session.session_goal.completed == frozenset()
    assert reviewer.invocations == 0


def test_prior_observations_support_completion_after_restore() -> None:
    session = SessionCore()
    turns = 0

    def chat(_message: str) -> TurnResult:
        nonlocal turns
        turns += 1
        if turns <= 2:
            action = ToolCallingTurnResult(
                1,
                1,
                1,
                False,
                True,
                tool_evidence=f"Tool: create_{turns}\nOutcome: success\nResult: CREATED_{turns}",
                evidence_success_count=1,
            )
            return TurnResult("cli_agent_handled", action, "")
        assert session.session_goal is not None
        restored = session_goal_from_payload(session_goal_to_payload(session.session_goal))
        assert restored is not None
        attach_session_goal(session, restored.with_completed(frozenset({0, 1})))
        return TurnResult(
            "cli_agent_handled",
            ToolCallingTurnResult(1, 1, 1, False, True, evidence_success_count=0),
            "Both resources created.",
        )

    class Reviewer(_ScriptedLLM):
        def invoke(self, messages: list[dict[str, Any]], **_kwargs: Any) -> AgentLLMResponse:
            if self.invocations >= 2:
                prompt = str(messages)
                assert "CREATED_1" in prompt and "CREATED_2" in prompt
            return super().invoke(messages)

    reviewer = Reviewer(
        [
            AgentLLMResponse(content='{"verdict":"NOT_REACHED"}'),
            AgentLLMResponse(content='{"verdict":"NOT_REACHED"}'),
            AgentLLMResponse(
                content='{"items":[{"index":0,"verdict":"VALID"},{"index":1,"verdict":"VALID"}]}'
            ),
            AgentLLMResponse(content='{"verdict":"GOAL_REACHED"}'),
        ]
    )
    outcome = run_until_session_goal(
        chat,
        session,
        "Create both resources",
        goal=SessionGoal(
            condition="Create both resources",
            checklist=("Create first", "Create second"),
            max_outer_turns=3,
        ),
        evaluate=build_session_goal_evaluator(lambda: reviewer),
    )
    assert outcome.goal.status == SessionGoalStatus.ACHIEVED
    assert outcome.turn_count == 3
    assert reviewer.invocations == 4


def test_evidence_overflow_remains_unverified_after_restore() -> None:
    goal = SessionGoal(condition="Deploy").with_finding("Deployed")
    goal = retain_tool_evidence(goal, "x" * 40000, succeeded=True)
    goal = retain_tool_evidence(goal, "y" * 40000 + "FAILED", succeeded=False)
    restored = session_goal_from_payload(session_goal_to_payload(goal))
    assert restored is not None
    assert restored.tool_evidence is None
    reviewer = _ScriptedLLM([AgentLLMResponse(content='{"verdict":"GOAL_REACHED"}')])
    verdict = evaluate_session_goal(restored, _result(), judge_llm=reviewer)
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert reviewer.invocations == 0
