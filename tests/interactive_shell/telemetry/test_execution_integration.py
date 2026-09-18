from __future__ import annotations

import io

from rich.console import Console

from infrastructure.analytics.prompt_log import recorder as prompt_log
from surfaces.interactive_shell.runtime.core.turn_accounting import (
    ToolCallingTurnResult,
)
from surfaces.interactive_shell.session import Session
from tests.shared.harness_turn_driver import run_harness_turn


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False)


def test_run_harness_turn_cli_agent_empty_response_is_recorded_empty(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(prompt_log, "capture_ai_generation", captured.append)

    def fake_execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
        )

    session = Session()
    output = io.StringIO()
    run_harness_turn(
        "show datadog integration details",
        session,
        Console(file=output, force_terminal=False, highlight=False),
        confirm_fn=None,
        is_tty=None,
        execute_actions=fake_execute,
    )

    assert output.getvalue() == ""
    assert len(captured) == 1
    assert captured[0]["$ai_input"][0]["content"] == "show datadog integration details"
    assert session.last_assistant_intent == "agent_completed"
