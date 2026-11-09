"""Unit tests for goal definition and verification of ReAct stop decisions."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

from core.agent.agent import Agent
from core.agent.goals import Goal, GoalObservation, goal_met, should_accept_with_goal
from core.llm.types import AgentLLMResponse, ToolCall
from core.tool.contracts import RegisteredTool


class _FakeLLM:
    def __init__(self, responses: Iterator[AgentLLMResponse]) -> None:
        self._responses = responses
        self.model_id: str | None = None

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [{"name": t.name} for t in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],  # noqa: ARG002
        *,
        system: str | None = None,  # noqa: ARG002
        tools: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> AgentLLMResponse:
        return next(self._responses)

    def build_assistant_message(self, content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [{"id": tc.id, "name": tc.name} for tc in tool_calls],
        }

    def build_tool_result_message(
        self, tool_calls: list[ToolCall], results: list[Any]
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "results": [{"id": tc.id, "output": output} for tc, output in zip(tool_calls, results)],
        }


class _FakeTool:
    def __init__(self, name: str = "query_logs") -> None:
        self.name = name

    def validate_public_input(self, value: dict[str, Any]) -> str | None:  # noqa: ARG002
        return None

    def extract_params(self, resolved: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        return {}

    def run(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002
        return {"ok": True}


def _text(content: str) -> AgentLLMResponse:
    return AgentLLMResponse(content=content, tool_calls=[], raw_content=None)


def _tool(name: str = "query_logs") -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[ToolCall(id="1", name=name, input={})],
        raw_content=None,
    )


def test_default_goal_requires_text_and_evidence() -> None:
    goal = Goal(description="count stars", success_criteria="numeric velocity")
    assert not goal_met(
        goal,
        GoalObservation(final_text="", evidence_count=2, iteration=0, max_iterations=5),
    )
    assert not goal_met(
        goal,
        GoalObservation(final_text="done", evidence_count=0, iteration=0, max_iterations=5),
    )
    assert goal_met(
        goal,
        GoalObservation(final_text="12 stars/day", evidence_count=1, iteration=0, max_iterations=5),
    )


def test_custom_verify_predicate() -> None:
    goal = Goal(
        description="stars",
        success_criteria="mentions velocity",
        verify=lambda obs: "velocity" in obs.final_text.lower(),
    )
    assert not goal_met(
        goal,
        GoalObservation(final_text="12 stars", evidence_count=3, iteration=0, max_iterations=5),
    )
    assert goal_met(
        goal,
        GoalObservation(
            final_text="star velocity is 12/day",
            evidence_count=0,
            iteration=0,
            max_iterations=5,
        ),
    )


def test_should_accept_nudges_until_ceiling() -> None:
    goal = Goal(description="investigate", success_criteria="root cause named")
    accept, nudge = should_accept_with_goal(
        goal,
        final_text="",
        evidence_count=0,
        iteration=0,
        max_iterations=3,
    )
    assert accept is False
    assert nudge is not None
    assert "Goal not yet met" in nudge

    accept, nudge = should_accept_with_goal(
        goal,
        final_text="",
        evidence_count=0,
        iteration=2,
        max_iterations=3,
    )
    assert accept is False
    assert nudge is not None


def test_should_accept_uses_goal_specific_nudge() -> None:
    goal = Goal(
        description="investigate",
        success_criteria="root cause named",
        verify=lambda _observation: False,
        nudge=lambda observation: f"Need more evidence after lap {observation.iteration + 1}.",
    )

    assert should_accept_with_goal(
        goal,
        final_text="not yet",
        evidence_count=1,
        iteration=0,
        max_iterations=3,
    ) == (False, "Need more evidence after lap 1.")


def test_no_goal_always_accepts() -> None:
    assert should_accept_with_goal(
        None,
        final_text="",
        evidence_count=0,
        iteration=0,
        max_iterations=5,
    ) == (True, None)


def test_missing_max_iterations_is_not_a_ceiling() -> None:
    """Unset budget must not make every request-driven turn look finished."""
    goal = Goal(description="investigate", success_criteria="root cause named")
    accept, nudge = should_accept_with_goal(
        goal,
        final_text="",
        evidence_count=0,
        iteration=0,
        max_iterations=None,
    )
    assert accept is False
    assert nudge is not None


def test_agent_goal_does_not_block_a_no_tool_reply() -> None:
    """A bare Goal without verify is not a stop gate; no-tool replies end the turn."""
    goal = Goal(description="count stars", success_criteria="numeric velocity")
    llm = _FakeLLM(iter([_text("not yet")]))
    tools = cast("list[RegisteredTool]", [_FakeTool()])
    agent: Agent[RegisteredTool] = Agent(
        llm=llm,
        system="sys",
        tools=tools,
        resolved_integrations={},
        max_iterations=2,
        goal=goal,
    )
    result = agent.run([{"role": "user", "content": "stars?"}])
    assert result.final_text == "not yet"


def test_agent_reviewed_goal_nudges_when_plan_incomplete() -> None:
    """Reviewed goals (with verify) reject premature stop and inject a nudge."""

    calls = {"n": 0}

    def _verify(observation: GoalObservation) -> bool:
        _ = observation
        calls["n"] += 1
        # Reject the first conclusion; accept after the nudge lap.
        return calls["n"] > 1

    goal = Goal(
        description="finish the plan",
        success_criteria="all steps done",
        verify=_verify,
        nudge=lambda _obs: "Keep going on the plan.",
    )
    llm = _FakeLLM(iter([_text("paused"), _tool("t1"), _text("done")]))
    tools = cast("list[RegisteredTool]", [_FakeTool()])
    agent: Agent[RegisteredTool] = Agent(
        llm=llm,
        system="sys",
        tools=tools,
        resolved_integrations={},
        max_iterations=6,
        goal=goal,
    )
    result = agent.run([{"role": "user", "content": "run the plan"}])
    assert result.final_text == "done"
    assert calls["n"] >= 2


def test_agent_without_goal_accepts_first_conclusion() -> None:
    llm = _FakeLLM(iter([_text("done")]))
    tools = cast("list[RegisteredTool]", [_FakeTool()])
    agent: Agent[RegisteredTool] = Agent(
        llm=llm,
        system="sys",
        tools=tools,
        resolved_integrations={},
        max_iterations=3,
    )
    result = agent.run([{"role": "user", "content": "hi"}])
    assert result.final_text == "done"


def test_goal_ceiling_uses_the_runtime_requests_budget() -> None:
    """The loop's run budget is what the goal observes, not construction-time max.

    An unmet goal still refuses at that last lap; the loop then emits an
    incomplete handoff instead of accepting an unsupported conclusion.
    """
    # Arrange: constructed for 10 laps, this run is budgeted 3.
    from unittest.mock import MagicMock

    from core.agent.agent import Agent
    from core.agent.goals import Goal

    agent = Agent(
        llm=MagicMock(),
        system="s",
        tools=[],
        max_iterations=10,
        goal=Goal(
            description="never satisfied",
            success_criteria="impossible",
            verify=lambda _observation: False,
        ),
    )
    agent._effective_max_iterations = 3

    # Act: the last lap of the *run* budget is iteration 2 (0-based).
    accept, _nudge = agent._should_accept_conclusion(
        evidence_count=0, iteration=2, final_text="done"
    )

    # Assert
    assert accept is False
