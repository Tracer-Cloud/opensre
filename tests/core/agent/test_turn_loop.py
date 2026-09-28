"""Shell-local turn loop bookkeeping tests."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness import OutputSink
from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.ports import ConfirmFn
from core.agent_harness.runtime import TurnPlan
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.turns.orchestrator import run_turn
from core.tool import ToolExecutionHooks
from infrastructure.analytics.prompt_log import recorder as prompt_log
from surfaces.interactive_shell.runtime.core.turn_accounting import (
    ToolCallingTurnResult,
)
from surfaces.interactive_shell.session import Session
from tests.shared.harness_turn_driver import run_harness_turn


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, color_system=None, width=80)


def _unhandled_turn(
    message: str,
    session: Session,
    console: Console,
    *,
    confirm_fn: ConfirmFn | None = None,
    is_tty: bool | None = None,
    request_exit: Callable[[], None] | None = None,
    turn_plan: TurnPlan | None = None,
    output: OutputSink | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> ToolCallingTurnResult:
    """A RunActionToolTurn seam whose action turn handles nothing."""
    return ToolCallingTurnResult(
        planned_count=0,
        executed_count=0,
        executed_success_count=0,
        has_unhandled_clause=False,
        handled=False,
        response_text="answered",
    )


def test_recorder_flushes_once_for_agent_answer(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(prompt_log, "capture_ai_generation", captured.append)

    result = run_harness_turn(
        "question",
        Session(),
        _console(),
        execute_actions=_unhandled_turn,
    )

    assert result.answered is True
    assert result.assistant_response_text == "answered"
    assert len(captured) == 1
    assert captured[0]["$ai_output_choices"][0]["content"] == "answered"


def test_recorder_flushes_once_for_silent_handled_turn(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(prompt_log, "capture_ai_generation", captured.append)
    session = Session()

    def _handled(
        message: str,
        session: Session,
        console: Console,
        *,
        confirm_fn: ConfirmFn | None = None,
        is_tty: bool | None = None,
        request_exit: Callable[[], None] | None = None,
        turn_plan: TurnPlan | None = None,
        output: OutputSink | None = None,
        tool_hooks: ToolExecutionHooks | None = None,
    ) -> ToolCallingTurnResult:
        """A RunActionToolTurn seam whose action turn handles the request."""
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="command output",
        )

    result = run_harness_turn(
        "run something",
        session,
        _console(),
        execute_actions=_handled,
    )

    assert result.answered is True
    assert result.final_intent == "agent_completed"
    assert len(captured) == 1
    assert captured[0]["$ai_output_choices"][0]["content"] == "command output"
    assert session.cli_agent_messages[-2:] == [
        ("user", "run something"),
        ("assistant", "command output"),
    ]


def test_default_turn_accounting_persists_action_only_context() -> None:
    storage = InMemorySessionStore()
    session = Session(store=storage)
    storage.open_session(session)

    def _handled(
        text: str,
        *,
        confirm_fn: ConfirmFn | None = None,
        is_tty: bool | None = None,
        turn_plan: Any = None,
    ) -> ToolCallingTurnResult:
        """An ExecuteActions seam whose action turn handles the request."""
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="Hawaii: +28C",
        )

    result = run_turn(
        "weather in Hawaii",
        session,
        execute_actions=_handled,
        accounting=DefaultTurnAccounting(session, "weather in Hawaii"),
    )

    records = storage.read(session.session_id)
    messages = [record for record in records if record.get("type") == "message"]

    assert result.final_intent == "agent_completed"
    assert session.cli_agent_messages[-2:] == [
        ("user", "weather in Hawaii"),
        ("assistant", "Hawaii: +28C"),
    ]
    assert [
        (message.get("role"), message.get("content"), message["metadata"]["kind"])
        for message in messages[-2:]
    ] == [
        ("user", "weather in Hawaii", "chat"),
        ("assistant", "Hawaii: +28C", "chat"),
    ]
