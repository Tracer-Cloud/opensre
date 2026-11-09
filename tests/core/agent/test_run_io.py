"""Unit tests for ``AgentRunInput`` assembly helpers in ``core.agent.run_io``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.agent.run_io import AgentRunInput
from core.messages import UserRuntimeMessage
from core.tool.contracts import AgentTool


def _tool(name: str = "inspect") -> AgentTool:
    return AgentTool(
        name=name,
        description=name,
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        execute=lambda _payload, _ctx: {"ok": True},
    )


@dataclass
class _PlainRuntimeRequest:
    system_prompt: str
    active_tools: tuple[AgentTool, ...]
    resolved_integrations: dict[str, Any]
    max_iterations: int
    text: str = "hello"

    def runtime_messages(self) -> list[UserRuntimeMessage]:
        return [UserRuntimeMessage(content=self.text)]


@dataclass
class _RenderedRuntimeRequest(_PlainRuntimeRequest):
    def render_system_prompt(self) -> str:
        return "rendered system"


def test_from_runtime_request_uses_render_system_prompt_when_callable() -> None:
    tool = _tool()
    llm = object()
    request = _RenderedRuntimeRequest(
        system_prompt="ignored envelope",
        active_tools=(tool,),
        resolved_integrations={"github": {"configured": True}},
        max_iterations=3,
    )

    run_input = AgentRunInput.from_runtime_request(request, llm=llm)

    assert run_input.llm is llm
    assert run_input.system == "rendered system"
    assert [t.name for t in run_input.tools] == ["inspect"]
    assert run_input.resolved == {"github": {"configured": True}}
    assert run_input.max_iterations == 3
    assert run_input.messages == [UserRuntimeMessage(content="hello")]


def test_from_runtime_request_copies_resolved_integrations() -> None:
    resolved_integrations = {"github": {"configured": True}}
    request = _PlainRuntimeRequest(
        system_prompt="sys",
        active_tools=(),
        resolved_integrations=resolved_integrations,
        max_iterations=1,
    )

    run_input = AgentRunInput.from_runtime_request(request, llm=object())

    # Mutate the source dict after construction. If `from_runtime_request`
    # stored a reference instead of a copy, this mutation would leak into
    # `run_input.resolved`.
    resolved_integrations["aws"] = {"configured": True}

    assert run_input.resolved == {"github": {"configured": True}}


def test_from_runtime_request_falls_back_to_system_prompt_string() -> None:
    tool = _tool()
    request = _PlainRuntimeRequest(
        system_prompt="plain system",
        active_tools=(tool,),
        resolved_integrations={},
        max_iterations=1,
    )

    run_input = AgentRunInput.from_runtime_request(request, llm=object())

    assert run_input.system == "plain system"


def test_from_runtime_request_includes_tool_resources_when_present() -> None:
    tool = _tool()

    @dataclass
    class _RequestWithResources:
        system_prompt: str
        active_tools: tuple[AgentTool, ...]
        resolved_integrations: dict[str, Any]
        max_iterations: int
        tool_resources: dict[str, Any]
        text: str = "hello"

        def runtime_messages(self) -> list[UserRuntimeMessage]:
            return [UserRuntimeMessage(content=self.text)]

    request = _RequestWithResources(
        system_prompt="sys",
        active_tools=(tool,),
        resolved_integrations={},
        max_iterations=1,
        tool_resources={"marker": "runtime"},
    )

    run_input = AgentRunInput.from_runtime_request(request, llm=object())

    assert run_input.tool_resources == {"marker": "runtime"}


def test_from_runtime_request_defaults_tool_resources_to_empty_dict() -> None:
    request = _PlainRuntimeRequest(
        system_prompt="sys",
        active_tools=(),
        resolved_integrations={},
        max_iterations=1,
    )

    run_input = AgentRunInput.from_runtime_request(request, llm=object())

    assert run_input.tool_resources == {}


def test_from_messages_normalizes_dict_messages() -> None:
    tool = _tool()
    llm = object()

    run_input = AgentRunInput.from_messages(
        [{"role": "user", "content": "hi"}],
        llm=llm,
        system="construction system",
        tools=[tool],
        resolved={"aws": {"configured": True}},
        tool_resources={"marker": "build"},
        max_iterations=4,
    )

    assert run_input.llm is llm
    assert run_input.system == "construction system"
    assert [t.name for t in run_input.tools] == ["inspect"]
    assert run_input.resolved == {"aws": {"configured": True}}
    assert run_input.tool_resources == {"marker": "build"}
    assert run_input.max_iterations == 4
    assert len(run_input.messages) == 1
    assert isinstance(run_input.messages[0], UserRuntimeMessage)
    assert run_input.messages[0].content == "hi"


def test_from_messages_defaults_none_tools_and_resolved() -> None:
    run_input = AgentRunInput.from_messages(
        [{"role": "user", "content": "ping"}],
        llm=object(),
        system="sys",
        tools=None,
        resolved=None,
        tool_resources={},
        max_iterations=1,
    )

    assert run_input.tools == []
    assert run_input.resolved == {}
