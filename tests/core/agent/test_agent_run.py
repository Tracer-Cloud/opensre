"""Unit tests for ``Agent.run`` validation and run-input resolution."""

from __future__ import annotations

from typing import Any

import pytest

from core.agent import Agent
from core.agent.run_io import AgentRunInput
from core.agent_harness.prompts import PromptEnvelope
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from core.llm.types import AgentLLMResponse
from core.tool.contracts import AgentTool


class _NoToolLLM:
    def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
        return []

    def invoke(
        self,
        _messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (system, tools)
        return AgentLLMResponse(content="ok", tool_calls=[], raw_content=None)

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[object]) -> dict[str, object]:
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}

    @staticmethod
    def build_tool_result_message(
        _tool_calls: list[object], _results: list[object]
    ) -> dict[str, object]:
        return {"role": "tool", "content": "[]"}


def _tool() -> AgentTool:
    return AgentTool(
        name="inspect",
        description="inspect",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        execute=lambda _payload, _ctx: {"ok": True},
    )


@pytest.mark.parametrize("tokens", [None, 0, 12])
def test_run_preserves_missing_and_measured_usage(tokens: int | None) -> None:
    class MeasuredLLM(_NoToolLLM):
        def invoke(self, *_args: Any, **_kwargs: Any) -> AgentLLMResponse:
            return AgentLLMResponse(content="answer", input_tokens=tokens, output_tokens=tokens)

    result = Agent(llm=MeasuredLLM(), system="test", tools=[], max_iterations=1).run(
        [{"role": "user", "content": "hello"}]
    )
    assert result.input_tokens == tokens
    assert result.output_tokens == tokens


def _runtime_request() -> TurnSnapshot:
    tool = _tool()
    return TurnSnapshot(
        text="turn",
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        reasoning_effort=None,
        system_prompt=PromptEnvelope.from_text("runtime system"),
        available_tools=(tool,),
        active_tools=(tool,),
        resolved_integrations={"github": {"configured": True}},
        max_iterations=2,
    )


def test_construction_requires_llm() -> None:
    with pytest.raises(ValueError, match="llm= must be set at construction"):
        Agent(
            llm=None,  # type: ignore[arg-type]
            system="sys",
            tools=[],
            resolved_integrations={},
            max_iterations=1,
        )


def test_construction_requires_llm_keyword() -> None:
    with pytest.raises(TypeError):
        Agent(system="sys", tools=[], resolved_integrations={}, max_iterations=1)  # type: ignore[call-arg]


def test_run_requires_initial_messages_or_runtime_request() -> None:
    agent = Agent(
        llm=_NoToolLLM(),
        system="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
    )

    with pytest.raises(ValueError, match="requires initial_messages or runtime_request"):
        agent.run()


def test_run_with_initial_messages_requires_system_at_construction() -> None:
    agent = Agent(
        llm=_NoToolLLM(),
        tools=[],
        resolved_integrations={},
        max_iterations=1,
    )

    with pytest.raises(ValueError, match="system= must be set"):
        agent.run([{"role": "user", "content": "hello"}])


def test_run_with_initial_messages_requires_max_iterations_at_construction() -> None:
    agent = Agent(
        llm=_NoToolLLM(),
        system="sys",
        tools=[],
        resolved_integrations={},
    )

    with pytest.raises(ValueError, match="max_iterations= must be set"):
        agent.run([{"role": "user", "content": "hello"}])


def test_build_run_input_from_runtime_request_uses_construction_llm() -> None:
    llm = _NoToolLLM()
    agent = Agent(
        llm=llm,
        system="ignored",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
    )
    ctx = _runtime_request()

    run_input = agent._build_run_input(None, ctx)

    assert isinstance(run_input, AgentRunInput)
    assert run_input.llm is llm
    assert run_input.system == "runtime system"


def test_build_run_input_from_messages_uses_construction_config() -> None:
    llm = _NoToolLLM()
    tool = _tool()
    agent = Agent(
        llm=llm,
        system="construction system",
        tools=[tool],
        resolved_integrations={"sentry": {"configured": True}},
        max_iterations=3,
        tool_resources={"marker": "instance"},
    )

    run_input = agent._build_run_input([{"role": "user", "content": "hello"}], None)

    assert run_input.llm is llm
    assert run_input.system == "construction system"
    assert [t.name for t in run_input.tools] == ["inspect"]
    assert run_input.resolved == {"sentry": {"configured": True}}
    assert run_input.tool_resources == {"marker": "instance"}
    assert run_input.max_iterations == 3


def test_runtime_request_path_uses_explicit_construction_llm() -> None:
    explicit = _NoToolLLM()

    agent = Agent(
        llm=explicit,
        system="ignored",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
    )

    run_input = agent._build_run_input(None, _runtime_request())

    assert run_input.llm is explicit
