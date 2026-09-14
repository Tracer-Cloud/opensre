"""ReAct loop emits Langfuse-shaped observations: one ``agent`` per run, one
``generation`` per model call (never aggregated), and each ``tool`` as a
sibling of the generation that requested it.

The noop default must leave the loop untouched — no payload copies, no
observation calls — which is what makes the backend safe to leave uninstalled.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from core.agent import Agent
from core.llm.types import AgentLLMResponse, ToolCall
from infrastructure.observability.trace.observations import (
    GenerationUsage,
    ObservationKind,
    is_observation_sink_active,
    set_observation_sink,
)
from tests.utils.observations import RecordingObservationSink


class _ToolThenDoneLLM:
    model_id = "test-model"

    def __init__(self) -> None:
        self.calls = 0

    def tool_schemas(self, tools: Sequence[Any]) -> list[dict[str, Any]]:
        return [
            {"name": tool.name, "description": "", "parameters": tool.parameters} for tool in tools
        ]

    def invoke(
        self,
        _messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (system, tools)
        self.calls += 1
        if self.calls == 1:
            return AgentLLMResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name="echo", input={"token": "ghp_" + "a" * 36})],
                stop_reason="tool_use",
                input_tokens=120,
                output_tokens=15,
                cache_read_tokens=100,
            )
        return AgentLLMResponse(content="done", input_tokens=200, output_tokens=5)

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[object]) -> dict[str, object]:
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}

    @staticmethod
    def build_tool_result_message(
        _tool_calls: list[object], _results: list[object]
    ) -> dict[str, object]:
        return {"role": "tool", "content": "[]"}


class _EchoTool:
    name = "echo"
    description = "echo"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"token": {"type": "string"}},
        "additionalProperties": False,
    }

    def validate_public_input(self, value: dict[str, Any]) -> str | None:
        _ = value
        return None

    def extract_params(self, resolved: dict[str, Any]) -> dict[str, Any]:
        return dict(resolved)

    def run(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "echoed": kwargs}


def _agent() -> Agent:
    return Agent(
        llm=_ToolThenDoneLLM(),
        system="sys",
        tools=[_EchoTool()],
        resolved_integrations={},
        max_iterations=3,
    )


def test_agent_run_emits_agent_generation_and_tool_observations() -> None:
    sink = RecordingObservationSink()
    set_observation_sink(sink)

    result = _agent().run([{"role": "user", "content": "hello"}])

    assert result.final_text == "done"
    agents = sink.of_kind(ObservationKind.AGENT)
    generations = sink.of_kind(ObservationKind.GENERATION)
    tools = sink.of_kind(ObservationKind.TOOL)
    assert [a.name for a in agents] == ["run-react-loop"]
    assert [g.name for g in generations] == ["think", "think"]
    assert [t.name for t in tools] == ["echo"]
    assert all(record.closed for record in sink.observations)

    agent = agents[0]
    assert agent.input == "hello"
    assert agent.output == "done"
    assert agent.metadata["tools"] == ["echo"]
    assert agent.metadata["stop_reason"] == "completed"
    assert agent.metadata["tool_call_count"] == 1
    assert not any(key.endswith("_tokens") for key in agent.metadata), (
        "token keys are key-redacted by the sink; usage belongs on generations"
    )

    first, second = generations
    assert first.parent is agent and second.parent is agent
    assert first.model == "test-model"
    assert first.input[0] == {"role": "system", "content": "sys"}
    assert first.input[-1]["role"] == "user"
    assert first.output["tool_calls"][0]["function"]["name"] == "echo"
    assert first.usage == GenerationUsage(
        input_tokens=120, output_tokens=15, cache_read_tokens=100, cache_creation_tokens=None
    )
    assert first.metadata["stop_reason"] == "tool_use"
    assert second.output == {"role": "assistant", "content": "done"}
    assert second.usage == GenerationUsage(input_tokens=200, output_tokens=5)

    tool = tools[0]
    assert tool.parent is agent, "a tool is a sibling of the generation that requested it"
    assert tool.metadata["tool_call_id"] == "c1"
    assert tool.metadata["is_error"] is False
    assert tool.output["ok"] is True
    assert tool.level is None


def test_noop_sink_skips_payload_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a backend the loop must not build generation payloads or call the sink."""
    assert is_observation_sink_active() is False

    def _unexpected(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("payload built while no sink is active")

    monkeypatch.setattr("core.agent.react_loop.generation_input", _unexpected)
    monkeypatch.setattr("core.agent.react_loop.generation_output", _unexpected)
    monkeypatch.setattr("core.tool.execution.public_tool_input", _unexpected)

    result = _agent().run([{"role": "user", "content": "hello"}])

    assert result.final_text == "done"
