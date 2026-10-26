"""``/effort`` reaches the model call of an action turn."""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from config.llm_reasoning_effort import ReasoningEffort, get_active_reasoning_effort
from core.llm.types import AgentLLMResponse
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.session import Session
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
    no_tool_response,
)


class _EffortLLM(FakeActionLLM):
    seen: list[str | None]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        self.seen.append(get_active_reasoning_effort())
        return super().invoke(messages, system=system, tools=tools)


def _turn(effort: ReasoningEffort | None) -> list[str | None]:
    session = Session()
    session.reasoning_effort = effort
    llm = _EffortLLM([no_tool_response("Hello.")])
    llm.seen = []
    run_action_tool_turn(
        "hello", session, Console(file=io.StringIO()), is_tty=False, llm_factory=lambda: llm
    )
    return llm.seen


def test_session_effort_applies_to_the_turns_model_calls(monkeypatch: Any) -> None:
    monkeypatch.delenv("OPENSRE_REASONING_EFFORT", raising=False)

    assert _turn(ReasoningEffort.LOW) == ["low"]
    assert _turn(ReasoningEffort.MAX) == ["xhigh"]
    # Without a session choice the provider default (or the env override) applies.
    assert _turn(None) == [None]
