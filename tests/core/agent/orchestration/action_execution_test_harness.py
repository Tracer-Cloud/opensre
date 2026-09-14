"""Typed fake agent harness for action-execution tests."""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

from core.agent_harness.ports import LlmFactory
from core.llm.types import AgentLLMResponse, SchemaDescribedTool, ToolCall


@dataclass
class FakeActionLLM:
    responses: list[AgentLLMResponse]
    invocations: int = 0
    tool_schema_names: list[str] = field(default_factory=list)
    model_id: str | None = None

    def tool_schemas(self, tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
        self.tool_schema_names = [str(tool.name) for tool in tools]
        return [{"name": tool.name} for tool in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],  # noqa: ARG002
        *,
        system: str | None = None,  # noqa: ARG002
        tools: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> AgentLLMResponse:
        self.invocations += 1
        if not self.responses:
            return AgentLLMResponse(content="", tool_calls=[], raw_content=None)
        return self.responses.pop(0)

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [{"id": tc.id, "name": tc.name, "input": tc.input} for tc in tool_calls],
        }

    @staticmethod
    def build_tool_result_message(
        tool_calls: list[ToolCall],
        results: list[Any],
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "results": [
                {"id": tc.id, "name": tc.name, "output": output}
                for tc, output in zip(tool_calls, results)
            ],
        }


@dataclass
class ActionExecutionHarness:
    llm: FakeActionLLM
    console_buffer: io.StringIO = field(default_factory=io.StringIO)

    @property
    def console(self) -> Console:
        return Console(file=self.console_buffer, force_terminal=False, highlight=False, width=100)

    @property
    def llm_factory(self) -> LlmFactory:
        return lambda: self.llm


def tool_response(name: str, args: dict[str, Any] | None = None) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[ToolCall(id=f"call_{name}", name=name, input=dict(args or {}))],
        raw_content=None,
    )


def no_tool_response(content: str = "") -> AgentLLMResponse:
    return AgentLLMResponse(content=content, tool_calls=[], raw_content=None)
