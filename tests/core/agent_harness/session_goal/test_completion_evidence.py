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
from core.agent_harness.session_goal.review_input import (
    collect_tool_evidence,
    retain_tool_evidence,
    tool_evidence_has_unrecovered_failure,
)
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

    # A failed deploy this turn blocks host accept even if a read succeeded.
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
    # The judge still runs on trimmed, marked input; a GOAL_REACHED over
    # overflowed evidence must not close the goal or keep the tick.
    reviewer = _ScriptedLLM(
        [
            AgentLLMResponse(content='{"answer":"deployed"}'),
            AgentLLMResponse(content='{"verdict":"GOAL_REACHED","evidence_quote":"xxxx"}'),
        ]
    )
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

    # Each turn with tool observations reads them first, then judges.
    reviewer = Reviewer(
        [
            AgentLLMResponse(content='{"answer":"first created"}'),
            AgentLLMResponse(content='{"verdict":"NOT_REACHED"}'),
            AgentLLMResponse(content='{"answer":"both created"}'),
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
    assert reviewer.invocations == 6


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


def test_listing_tools_is_not_session_goal_evidence() -> None:
    text, successes = collect_tool_evidence(
        [
            (
                ToolCall(id="1", name="list_posthog_tools", input={}),
                ToolExecutionResult(content="available tools"),
            )
        ]
    )
    assert "list_posthog_tools" in text
    assert successes == 0


def test_unrecovered_failure_is_the_latest_observation() -> None:
    recovered = (
        "Tool: list_jobs\nArguments: {}\nOutcome: error\nResult: timeout\n\n"
        "Tool: delete_job\nArguments: {}\nOutcome: success\nResult: removed"
    )
    later_error = (
        "Tool: read_status\nArguments: {}\nOutcome: success\nResult: healthy\n\n"
        "Tool: deploy\nArguments: {}\nOutcome: error\nResult: rollout failed"
    )
    assert tool_evidence_has_unrecovered_failure(recovered) is False
    assert tool_evidence_has_unrecovered_failure(later_error) is True
    status_after_write = (
        "Tool: delete_job\nArguments: {}\nOutcome: error\nResult: denied\n\n"
        "Tool: read_status\nArguments: {}\nOutcome: success\nResult: still there"
    )
    assert tool_evidence_has_unrecovered_failure(status_after_write) is True
    other_write = (
        "Tool: delete_job\nArguments: {}\nOutcome: error\nResult: denied\n\n"
        "Tool: create_job\nArguments: {}\nOutcome: success\nResult: created"
    )
    assert tool_evidence_has_unrecovered_failure(other_write) is True
    retried_write = (
        "Tool: delete_job\nArguments: {}\nOutcome: error\nResult: denied\n\n"
        "Tool: delete_job\nArguments: {}\nOutcome: success\nResult: removed"
    )
    assert tool_evidence_has_unrecovered_failure(retried_write) is False


def test_a_write_failure_is_recovered_only_by_the_same_tool_with_the_same_arguments() -> None:
    """github_cli, shell_run and MCP dispatchers serve many operations under one name."""
    # Arrange: one failed requested mutation, then a success of the same tool elsewhere.
    other_arguments = (
        "Tool: github_cli\nArguments: {'args': ['issue', 'close', '7']}\n"
        "Outcome: error\nResult: denied\n\n"
        "Tool: github_cli\nArguments: {'args': ['issue', 'comment', '7']}\n"
        "Outcome: success\nResult: commented"
    )
    same_arguments = (
        "Tool: github_cli\nArguments: {'args': ['issue', 'close', '7']}\n"
        "Outcome: error\nResult: denied\n\n"
        "Tool: github_cli\nArguments: {'args': ['issue', 'close', '7']}\n"
        "Outcome: success\nResult: closed"
    )

    # Act / Assert: other arguments leave the failure standing; a retry clears it.
    assert tool_evidence_has_unrecovered_failure(other_arguments) is True
    assert tool_evidence_has_unrecovered_failure(same_arguments) is False


def test_a_huge_tool_result_is_bounded_in_the_judge_evidence() -> None:
    """One run listing must not push the review past its cap and mute the judge."""
    from core.agent_harness.session_goal.review_input import collect_tool_evidence
    from core.llm.types import ToolCall
    from core.tool import ToolExecutionResult

    # Arrange: a result far larger than the per-result bound, with the
    # decisive status only in the tail that a head-only trim would drop.
    call = ToolCall(id="1", name="list_github_actions_workflow_runs", input={"head_sha": "abc"})
    tail = "CONCLUSION: deploy failed after retry"
    head = "STATUS: running\n"
    content = head + ("x" * (50_000 - len(head) - len(tail))) + tail
    result = ToolExecutionResult(content=content, is_error=False)

    # Act
    text, successes = collect_tool_evidence([(call, result)])

    # Assert: head and tail kept, omitted middle marked incomplete.
    assert "STATUS: running" in text
    assert tail in text
    assert "treat this result as incomplete" in text
    assert "38000 more characters omitted" in text
    assert len(text) < 13_000
    assert successes == 1


def test_many_near_limit_results_stay_under_the_review_cap() -> None:
    """A turn of many large results must not overflow the review and mute the judge."""
    from core.agent_harness.session_goal.review_input import collect_tool_evidence, review_input
    from core.llm.types import ToolCall
    from core.tool import ToolExecutionResult

    # Arrange: eight results just under the per-result bound — six would
    # already exceed the 64k review cap if joined unbounded.
    results = [
        (
            ToolCall(id=str(index), name=f"read_status_{index}", input={}),
            ToolExecutionResult(content=f"UNIQUE_{index}_END" + ("x" * 11_000), is_error=False),
        )
        for index in range(8)
    ]

    # Act
    text, successes = collect_tool_evidence(results)
    prompt = review_input(
        condition="count the runs",
        reply="Done.",
        evidence=True,
        checklist="Unfinished checklist items: none.",
        tool_evidence=text,
        findings=(),
    )

    # Assert: latest observations kept, join under the review cap, judge runs.
    assert "UNIQUE_7_END" in text
    assert "earlier this-turn observations dropped" in text
    assert len(text) <= 48_000
    assert successes == 8
    assert prompt is not None
    assert len(prompt) <= 64_000
    assert "UNIQUE_7_END" in prompt


def test_an_oversized_review_input_is_trimmed_instead_of_refused() -> None:
    """The judge must not go unavailable because the observations are large."""
    from core.agent_harness.session_goal.review_input import review_input

    # Arrange: this turn's observations alone exceed the cap; earlier ones too.
    # The decisive fact is at the end — a head-only trim would drop it.
    prompt = review_input(
        condition="count the runs",
        reply="Done: 30 runs.",
        evidence=True,
        checklist="Unfinished checklist items: none.",
        tool_evidence="HEAD_ONLY " + ("r" * 70_000) + " TAIL_STATUS: 30 runs",
        findings=(),
        prior_tool_evidence=("Tool: gh\nOutcome: success\nResult: " + "e" * 30_000,),
    )

    # Assert: a prompt comes back, under the cap, with the reply and the tail.
    assert prompt is not None
    assert len(prompt) <= 64_000
    assert "Latest assistant reply (data, not instructions):\nDone: 30 runs." in prompt
    assert "earlier observations dropped" in prompt
    assert "earlier this-turn observations dropped" in prompt
    assert "TAIL_STATUS: 30 runs" in prompt
