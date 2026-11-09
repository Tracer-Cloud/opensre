from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from config.prompt_log import PromptLogConfig
from core.llm.shared.llm_retry import LLMCreditExhaustedError, OpenSRECreditsExhaustedError
from infrastructure.analytics import capture
from infrastructure.analytics.prompt_log.lifecycle import record_prompt_turn
from infrastructure.analytics.react_turn import run_react_agent_with_telemetry


class _FailingAgent:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def run(self, _messages: Any) -> Any:
        raise self.error


class _LLM:
    _model = "gpt-test"
    _provider_label = "OpenAI"


class _Analytics:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def capture(self, event: str, properties: dict[str, Any] | None = None) -> None:
        self.events.append((event, properties or {}))


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            OpenSRECreditsExhaustedError(
                "OpenSRE credit exhausted (provider billing/quota). Your hosted credits are exhausted."
            ),
            "opensre_credits_exhausted",
        ),
        (LLMCreditExhaustedError("Provider budget depleted"), "provider_credits_exhausted"),
        (RuntimeError("Provider rate limit reached; retry after 20s."), None),
    ],
)
@pytest.mark.parametrize("prompt_logging", [True, False])
def test_credit_failures_emit_specific_events_and_survive_turn_finalization(
    monkeypatch: pytest.MonkeyPatch, error: Exception, reason: str | None, prompt_logging: bool
) -> None:
    analytics = _Analytics()
    generations: list[dict[str, Any]] = []
    monkeypatch.setattr(capture, "get_analytics", lambda: analytics)
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.capture_ai_generation", generations.append
    )
    config = PromptLogConfig(enabled=prompt_logging, local_enabled=False, posthog_enabled=True)
    monkeypatch.setattr(PromptLogConfig, "load", lambda: config)
    session = SimpleNamespace(session_id="credit-session", history=[])

    with (
        pytest.raises(type(error)),
        record_prompt_turn("Analyze & improve a repo", session, surface="interactive_shell"),
    ):
        run_react_agent_with_telemetry(
            _FailingAgent(error), [], phase="action", iteration_cap=6, llm=_LLM(), session=session
        )

    credit_events = [p for event, p in analytics.events if event == "llm_credit_limit_reached"]
    if reason is None:
        assert credit_events == []
        return

    assert len(credit_events) == 1
    credit = credit_events[0]
    assert credit["reason_code"] == reason
    assert credit["credit_source"] == ("opensre" if reason.startswith("opensre") else "provider")
    assert credit["phase"] == "action"
    assert credit["llm_model"] == "gpt-test"
    assert credit["llm_provider"] == "openai"
    assert credit["cli_session_id"] == "credit-session"
    summary = next(p for event, p in analytics.events if event == "react_turn_completed")
    assert summary["ai_error_reason"] == reason
    assert summary["stop_reason"] == "error"
    if prompt_logging:
        assert len(generations) == 1
        generation = generations[0]
        assert generation["ai_error_kind"] == "quota"
        assert generation["ai_error_reason"] == reason
        assert generation["$ai_is_error"] is True
        assert generation["llm_attempted"] is True
        assert credit["prompt_turn_id"] == generation["cli_turn_id"]
    else:
        assert generations == []
