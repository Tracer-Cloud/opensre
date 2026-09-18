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


def test_shell_cancel_reaches_recorder_and_rebound_sink_starts_fresh(monkeypatch) -> None:
    import threading

    from surfaces.interactive_shell.runtime.agent_harness_adapters import ShellOutputSink
    from surfaces.interactive_shell.runtime.core.state import SpinnerState
    from surfaces.interactive_shell.ui.streaming.console import StreamingConsole

    captured = []
    monkeypatch.setattr(prompt_log, "capture_ai_generation", captured.append)
    cancel = threading.Event()
    console = StreamingConsole(SpinnerState(), cancel, output=_console())
    sink = ShellOutputSink(console)
    session = Session()

    def completed_reply(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(0, 0, 0, False, True, response_text="Finished reply")

    def cancelled_reply(*args: object, **kwargs: object) -> ToolCallingTurnResult:
        # The provider returns after the UI has requested cancellation.
        cancel.set()
        return completed_reply(*args, **kwargs)

    result = run_harness_turn(
        "Stop this work", session, console, output=sink, execute_actions=cancelled_reply
    )
    assert result.cancelled
    assert captured[0]["$ai_is_error"] is True
    assert captured[0]["error_kind"] == "cancelled"
    assert captured[0]["$ai_input"][0]["content"] == "Stop this work"
    assert "Finished reply" not in str(captured[0]["$ai_output_choices"])

    next_console = StreamingConsole(SpinnerState(), threading.Event(), output=_console())
    sink.bind_console(next_console)
    next_result = run_harness_turn(
        "Fresh work", session, next_console, output=sink, execute_actions=completed_reply
    )
    assert not next_result.cancelled
    assert len(captured) == 2
    assert not captured[1].get("$ai_is_error")

    from surfaces.interactive_shell.runtime.shell_turn_execution import execute_shell_turn

    before = len(captured)
    # Cancellation before admission must not be mislabeled as capacity rejection.
    pre_cancelled = execute_shell_turn("Already stopped", Session(), console)
    assert pre_cancelled.cancelled
    assert pre_cancelled.final_intent == "cli_agent_cancelled"
    assert len(captured) == before
