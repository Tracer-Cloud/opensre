"""Prompt events must survive the real ask/harness lifecycle, including provider failures."""

from __future__ import annotations

import signal
from typing import Any

import pytest

from config.prompt_log import PromptLogConfig
from core.agent.run_io import AgentRunResult
from core.agent_harness.harness import AgentSession, SessionStartupResult
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.turns import action_driver
from infrastructure.analytics import provider
from infrastructure.analytics.events import Event
from surfaces.cli.ask import service
from surfaces.cli.ask.signals import AskSignal


class _Analytics:
    def __init__(self) -> None:
        self.events: list[tuple[Event, dict[str, Any]]] = []

    def capture(self, event: Event, properties: dict[str, Any] | None = None) -> None:
        self.events.append((event, properties or {}))


class _LLM:
    _model = "capture-test-model"
    _provider_label = "OpenAI"


class _Agent:
    def __init__(self, error: BaseException | None) -> None:
        self.error = error
        self._react_iterations_used = 1
        self._react_executed: list[Any] = []
        self._react_hit_iteration_cap = False

    def run(self, _messages: Any) -> AgentRunResult:
        if self.error is not None:
            raise self.error
        return AgentRunResult(
            messages=[],
            final_text="Recorded answer",
            executed=[],
            llm_iterations_used=1,
            input_tokens=12,
            output_tokens=3,
        )


@pytest.mark.parametrize("failure", [None, "provider", "cancelled"])
def test_ask_captures_prompt_and_links_run_on_every_outcome(monkeypatch, tmp_path, failure) -> None:
    analytics = _Analytics()
    monkeypatch.setattr(provider, "get_analytics", lambda: analytics)
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.sinks.posthog_ai.get_analytics", lambda: analytics
    )
    # capture.py imports the factory directly.
    monkeypatch.setattr("infrastructure.analytics.capture.get_analytics", lambda: analytics)
    cfg = PromptLogConfig(log_path=tmp_path / "prompts.jsonl")
    monkeypatch.setattr(PromptLogConfig, "load", lambda: cfg)
    session = SessionCore(store=InMemorySessionStore())
    session.resolved_integrations_cache = {}
    monkeypatch.setattr(
        AgentSession, "startup", lambda _self: SessionStartupResult(session=session, prompts=None)
    )
    error = (
        RuntimeError("provider failed with Bearer secret-token-12345678901234567890")
        if failure == "provider"
        else AskSignal(signal.SIGTERM)
        if failure == "cancelled"
        else None
    )

    def build_agent(**kwargs: Any) -> action_driver.ActionTurnPlan:
        return action_driver.ActionTurnPlan(
            agent=_Agent(error),
            user_message=kwargs["message"],
            llm=_LLM(),
            max_iterations=64,
        )

    monkeypatch.setattr(action_driver, "_build_action_agent", build_agent)
    # A caller may recover from an unrelated failure by starting an ask turn.
    # That handled exception must not mark a successful agent run as failed.
    try:
        raise RuntimeError("unrelated handled failure")
    except RuntimeError:
        outcome = service.run_ask(
            "Explain the outage", allowed_tools=(), bypass_approvals=False, ephemeral=True
        )

    expected = (
        service.AskStatus.SUCCESS
        if failure is None
        else (service.AskStatus.CANCELLED if failure == "cancelled" else service.AskStatus.ERROR)
    )
    assert outcome.status == expected
    generations = [props for event, props in analytics.events if event == Event.AI_GENERATION]
    assert len(generations) == 1, "ask emitted no prompt exchange"
    generation = generations[0]
    assert generation["$ai_input"] == [{"role": "user", "content": "Explain the outage"}]
    assert generation["cli_session_id"] == session.session_id
    assert generation["cli_turn_id"]
    assert generation["$ai_model"] == "capture-test-model"
    assert "secret-token-12345678901234567890" not in str(generation)
    runs = [props for event, props in analytics.events if event == Event.REACT_TURN_COMPLETED]
    assert len(runs) == 1
    assert runs[0]["prompt_turn_id"] == generation["cli_turn_id"]
    if failure:
        assert generation["$ai_is_error"] is True
    else:
        assert not generation.get("$ai_is_error")
        assert runs[0]["stop_reason"] == "no_tools_needed"
        assert generation["$ai_output_choices"] == [
            {"role": "assistant", "content": "Recorded answer"}
        ]
        assert generation["$ai_input_tokens"] == 12
        assert generation["$ai_output_tokens"] == 3
