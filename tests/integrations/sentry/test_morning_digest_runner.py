"""Tests for the Sentry morning digest headless runner."""

from __future__ import annotations

import pytest

from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from integrations.sentry.morning_digest_runner import (
    build_morning_digest_prompt,
    run_sentry_morning_digest,
)


class TestBuildMorningDigestPrompt:
    def test_default_prompt(self) -> None:
        prompt = build_morning_digest_prompt({})
        assert "24 hours" in prompt
        assert "summarizing-sentry-issues" in prompt

    def test_includes_uptime_instruction(self) -> None:
        prompt = build_morning_digest_prompt({})
        assert "uptime" in prompt.lower()

    def test_project_scope(self) -> None:
        prompt = build_morning_digest_prompt({"project_slug": "checkout-api"})
        assert "checkout-api" in prompt


class TestRunSentryMorningDigest:
    def test_raises_when_sentry_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.sentry.morning_digest_runner.configured_integration_services",
            lambda: ("datadog",),
        )

        with pytest.raises(RuntimeError, match="Sentry is not configured"):
            run_sentry_morning_digest({})

    def test_raises_when_llm_does_not_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.sentry.morning_digest_runner.configured_integration_services",
            lambda: ("sentry",),
        )
        monkeypatch.setattr(
            "integrations.sentry.morning_digest_runner._dispatch_headless_turn",
            lambda _message, _payload: TurnResult(
                final_intent="chat",
                action_result=ToolCallingTurnResult(
                    planned_count=0,
                    executed_count=0,
                    executed_success_count=0,
                    has_unhandled_clause=False,
                    handled=True,
                ),
                assistant_response_text="",
            ),
        )

        with pytest.raises(RuntimeError, match="did not produce a response"):
            run_sentry_morning_digest({})

    def test_returns_assistant_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.sentry.morning_digest_runner.configured_integration_services",
            lambda: ("sentry",),
        )
        monkeypatch.setattr(
            "integrations.sentry.morning_digest_runner._dispatch_headless_turn",
            lambda _message, _payload: TurnResult(
                final_intent="chat",
                action_result=ToolCallingTurnResult(
                    planned_count=1,
                    executed_count=1,
                    executed_success_count=1,
                    has_unhandled_clause=False,
                    handled=True,
                ),
                assistant_response_text="## Morning digest\nCheckout errors dominate.",
            ),
        )

        report = run_sentry_morning_digest({"project_slug": "api"})
        assert "Checkout errors dominate." in report
