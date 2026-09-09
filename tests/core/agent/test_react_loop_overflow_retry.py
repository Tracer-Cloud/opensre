"""A request the provider rejects as too large is retried once under a tighter budget."""

from __future__ import annotations

from typing import Any

import pytest

from core.agent import Agent
from core.llm.types import AgentLLMResponse

_TOO_LARGE = (
    "OpenAI API failed: Error code: 413 - {'error': {'message': "
    "'The LLM request is too large.', 'code': 'request_too_large'}}"
)


class _RejectingLLM:
    """Rejects the first ``failures`` calls as too large, then answers."""

    model_id = "test-model"

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls: list[list[dict[str, Any]]] = []

    def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
        return []

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (system, tools)
        self.calls.append(list(messages))
        if len(self.calls) <= self.failures:
            raise RuntimeError(_TOO_LARGE)
        return AgentLLMResponse(content="done")

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[object]) -> dict[str, object]:
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}

    @staticmethod
    def build_tool_result_message(
        _tool_calls: list[object], _results: list[object]
    ) -> dict[str, object]:
        return {"role": "tool", "content": "[]"}


def _agent(llm: _RejectingLLM) -> Agent:
    return Agent(
        llm=llm,
        system="sys",
        tools=[],
        resolved_integrations={},
        tool_resources={},
        max_iterations=2,
    )


def test_a_too_large_rejection_is_retried_once() -> None:
    # Arrange: the first call is rejected as too large.
    llm = _RejectingLLM(failures=1)

    # Act
    result = _agent(llm).run([{"role": "user", "content": "hello"}])

    # Assert: one retry, the run completes.
    assert len(llm.calls) == 2
    assert result.final_text == "done"


def test_a_second_too_large_rejection_propagates() -> None:
    # Arrange: every call is rejected.
    llm = _RejectingLLM(failures=2)

    # Act / Assert: no endless retry loop, the error reaches the caller.
    with pytest.raises(RuntimeError, match="request_too_large"):
        _agent(llm).run([{"role": "user", "content": "hello"}])
    assert len(llm.calls) == 2
