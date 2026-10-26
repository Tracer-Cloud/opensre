from __future__ import annotations

from typing import Any

import pytest

from core.agent.run_io import AgentRunResult
from infrastructure.analytics import capture
from infrastructure.analytics.events import Event
from infrastructure.analytics.react_turn import run_react_agent_with_telemetry


class _StubAnalytics:
    def __init__(self) -> None:
        self.events: list[tuple[Event, dict[str, object] | None]] = []

    def capture(self, event: Event, properties: dict[str, object] | None = None) -> None:
        self.events.append((event, properties))


class _StubAgent:
    def __init__(
        self,
        *,
        result: AgentRunResult | None = None,
        error: Exception | None = None,
        iterations_used: int = 0,
    ) -> None:
        self._result = result
        self._error = error
        self._react_iterations_used = iterations_used
        self._react_executed: list[tuple[Any, Any]] = []
        self._react_hit_iteration_cap = False

    def run(self, _initial_messages: list[dict[str, Any]]) -> AgentRunResult:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


class _StubLLM:
    _model = "gpt-test"
    _provider_label = "OpenAI"


def test_run_react_agent_with_telemetry_emits_one_completed_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)
    agent = _StubAgent(
        result=AgentRunResult(
            messages=[],
            final_text="done",
            executed=[],
            llm_iterations_used=1,
        )
    )

    result = run_react_agent_with_telemetry(
        agent,  # type: ignore[arg-type]
        [{"role": "user", "content": "hello"}],
        phase="action",
        iteration_cap=6,
        llm=_StubLLM(),
    )

    assert result.final_text == "done"
    assert len(stub.events) == 1
    event, properties = stub.events[0]
    assert event == Event.REACT_TURN_COMPLETED
    assert properties is not None
    assert properties["phase"] == "action"
    assert properties["stop_reason"] == "no_tools_needed"
    assert properties["llm_iterations_used"] == 1


def test_run_react_agent_with_telemetry_emits_error_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)
    agent = _StubAgent(error=RuntimeError("provider down"))

    with pytest.raises(RuntimeError, match="provider down"):
        run_react_agent_with_telemetry(
            agent,  # type: ignore[arg-type]
            [{"role": "user", "content": "hello"}],
            phase="gather",
            iteration_cap=4,
            llm=_StubLLM(),
        )

    assert len(stub.events) == 1
    event, properties = stub.events[0]
    assert event == Event.REACT_TURN_COMPLETED
    assert properties is not None
    assert properties["phase"] == "gather"
    assert properties["stop_reason"] == "error"
    assert properties["llm_iterations_used"] == 0


def test_run_react_agent_with_telemetry_reports_partial_iterations_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)
    agent = _StubAgent(error=RuntimeError("provider down"), iterations_used=3)

    with pytest.raises(RuntimeError, match="provider down"):
        run_react_agent_with_telemetry(
            agent,  # type: ignore[arg-type]
            [{"role": "user", "content": "hello"}],
            phase="action",
            iteration_cap=6,
            llm=_StubLLM(),
        )

    assert len(stub.events) == 1
    _event, properties = stub.events[0]
    assert properties is not None
    assert properties["llm_iterations_used"] == 3
