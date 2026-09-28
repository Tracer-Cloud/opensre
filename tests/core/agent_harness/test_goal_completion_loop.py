"""Completion checks exercise the real tool loop, independently of skill wording."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import BaseModel

from core.agent import Agent
from core.agent_harness.turns.goal_review import build_goal_reviewer, tap_executed_tool_names
from core.agent_harness.turns.work_outcome import (
    ExecutedToolOutcome,
    tap_executed_tool_outcomes,
)
from core.llm.types import AgentLLMResponse, ToolCall


class _LLM:
    model_id = "test"

    def __init__(self, replies: list[AgentLLMResponse], verdicts: list[dict[str, str]]) -> None:
        self.replies = iter(replies)
        self.verdicts: Iterator[dict[str, str]] = iter(verdicts)
        self.reviews: list[str] = []

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [{"name": tool.name} for tool in tools]

    def invoke(self, messages: Any, **kwargs: Any) -> AgentLLMResponse:  # noqa: ARG002
        return next(self.replies)

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

    def with_structured_output(self, schema: type[BaseModel]) -> Any:
        owner = self

        class Review:
            def invoke(self, prompt: str) -> BaseModel:
                owner.reviews.append(prompt)
                return schema.model_validate(next(owner.verdicts))

        return Review()


class _Tool:
    name = "shell_run"

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = iter(results)

    def validate_public_input(self, value: Any) -> None:  # noqa: ARG002
        return None

    def extract_params(self, resolved: Any) -> dict[str, Any]:  # noqa: ARG002
        return {}

    def run(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002
        return next(self.results)


def _call(command: str) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="", tool_calls=[ToolCall(id=command, name="shell_run", input={"command": command})]
    )


def _run(llm: _LLM, tool: _Tool, request: str, *, max_iterations: int = 12) -> Any:
    names: list[str] = []
    outcomes: list[ExecutedToolOutcome] = []
    on_event = tap_executed_tool_names(None, names)
    on_event = tap_executed_tool_outcomes(on_event, outcomes)
    agent = Agent(
        llm=llm,
        system="Follow the loaded workflow. It may suggest unrelated repository metrics.",
        tools=[tool],
        max_iterations=max_iterations,
        max_stagnant_iterations=3,
        goal=build_goal_reviewer(llm, request, names, executed_outcomes=outcomes),
        on_runtime_event=on_event,
    )
    return agent.run([{"role": "user", "content": request}])


def test_wrong_metric_and_closing_question_do_not_finish_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSRE_REACT_GOAL_LLM_REVIEW", "1")
    llm = _LLM(
        [
            _call("gh api repos/acme/app --jq .forks_count"),
            AgentLLMResponse(content="The repository has 17 forks. Anything else?"),
            _call("gh api repos/acme/app --jq .stargazers_count"),
            AgentLLMResponse(content="acme/app has 420 stars."),
        ],
        [
            {"verdict": "NOT_REACHED", "reason": "Fetch stars for acme/app; forks are unrelated."},
            {"verdict": "GOAL_REACHED", "reason": "The star count is supported."},
        ],
    )
    result = _run(
        llm, _Tool([{"forks_count": 17}, {"stargazers_count": 420}]), "Stars for acme/app?"
    )

    assert result.final_text == "acme/app has 420 stars."
    assert len(result.executed) == 2
    assert len(llm.reviews) == 1
    assert "forks_count" in llm.reviews[0] and "17" in llm.reviews[0]
    assert "Stars for acme/app?" in llm.reviews[0]


def test_failed_command_recovers_before_reporting_security_fix() -> None:
    llm = _LLM(
        [
            _call("curl security-check"),
            AgentLLMResponse(content="curl failed, so I stopped."),
            _call("inspect-and-fix"),
            _call("verify-fix"),
            AgentLLMResponse(content="Fixed and verified the vulnerable code."),
        ],
        [
            {"verdict": "NOT_REACHED", "reason": "The command failed; inspect locally and repair."},
            {"verdict": "GOAL_REACHED", "reason": "The repair and verification succeeded."},
        ],
    )
    result = _run(
        llm,
        _Tool([{"ok": False, "exit_code": 7}, {"patched": True}, {"tests_passed": True}]),
        "Fix security bugs in acme/app and verify the fixes.",
    )

    assert result.final_text == "Fixed and verified the vulnerable code."
    assert len(result.executed) == 3
    # Host gate, not the optional LLM reviewer: a failed curl is enough.
    assert llm.reviews == []


def test_rejected_answer_at_iteration_ceiling_is_not_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSRE_REACT_GOAL_LLM_REVIEW", "1")
    llm = _LLM(
        [_call("get-forks"), AgentLLMResponse(content="Done: 17 forks.")],
        [{"verdict": "NOT_REACHED", "reason": "Stars are still missing."}],
    )
    result = _run(llm, _Tool([{"forks_count": 17}]), "Get stars.", max_iterations=2)

    assert result.hit_iteration_cap
    assert result.final_text != "Done: 17 forks."
    assert "could not complete" in result.final_text
