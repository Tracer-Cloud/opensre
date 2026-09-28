"""Host cooperative cancel short-circuits gather/answer and labels the turn."""

from __future__ import annotations

import threading
from typing import Any

from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.turns.host_cancel import host_cancel_requested
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import (
    FINAL_INTENT_CANCELLED,
    ToolCallingTurnResult,
    TurnResult,
)


class _Accounting:
    def record_action_result(self, _result: ToolCallingTurnResult) -> None:
        return None

    def finalize(self, result: TurnResult) -> TurnResult:
        return result


class _CancelSink:
    def __init__(self) -> None:
        self.turn_cancel = threading.Event()
        self.streamed: list[str] = []

    def print(self, message: str = "") -> None:
        _ = message

    def render_response_header(self, label: str) -> None:
        _ = label

    def render_error(self, message: str) -> None:
        _ = message

    def stream(self, *, label: str, chunks: Any, **_kwargs: Any) -> str:
        _ = label
        parts = list(chunks)
        text = "".join(str(p) for p in parts)
        self.streamed.append(text)
        return text

    def finalize(self, answer: str) -> None:
        _ = answer


def test_host_cancel_requested_reads_sink_event() -> None:
    from core.agent_harness.turns.host_cancel import ensure_turn_cancel

    sink = _CancelSink()
    assert host_cancel_requested(sink) is False
    ensure_turn_cancel(sink).set()
    assert host_cancel_requested(sink) is True
    assert host_cancel_requested(None) is False


def test_run_turn_cancelled_action_stops_turn() -> None:
    def execute_actions(_text: str, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            0,
            0,
            0,
            False,
            False,
            cancelled=True,
        )

    result = run_turn(
        "question?",
        SessionCore(store=InMemorySessionStore()),
        execute_actions=execute_actions,
        accounting=_Accounting(),
        output=_CancelSink(),
    )
    assert result.final_intent == FINAL_INTENT_CANCELLED
    assert result.cancelled is True
    assert result.answered is False


def test_run_turn_cancel_after_action_stops_turn() -> None:
    sink = _CancelSink()

    def execute_actions(_text: str, **_kwargs: object) -> ToolCallingTurnResult:
        sink.turn_cancel.set()
        return ToolCallingTurnResult(0, 0, 0, False, False)

    result = run_turn(
        "question?",
        SessionCore(store=InMemorySessionStore()),
        execute_actions=execute_actions,
        accounting=_Accounting(),
        output=sink,
    )
    assert result.final_intent == FINAL_INTENT_CANCELLED


def test_bindable_output_stream_stops_when_turn_cancel_set() -> None:
    from infrastructure.turn_host.bindable_output import BindableOutput

    class _Inner:
        def __init__(self) -> None:
            self.turn_cancel = threading.Event()
            self.seen: list[str] = []

        def stream(self, *, label: str, chunks: Any, **_kwargs: Any) -> str:
            _ = label
            parts: list[str] = []
            for chunk in chunks:
                self.seen.append(str(chunk))
                parts.append(str(chunk))
            return "".join(parts)

        def finalize(self, answer: str) -> None:
            _ = answer

        def render_error(self, message: str) -> None:
            _ = message

        def set_tool_status(self, status: str) -> None:
            _ = status

    inner = _Inner()
    bindable = BindableOutput()
    bindable.bind(inner)  # type: ignore[arg-type]

    def _chunks() -> Any:
        yield "a"
        inner.turn_cancel.set()
        yield "b"
        yield "c"

    text = bindable.stream(label="assistant", chunks=_chunks())
    assert text == "a"
    assert inner.seen == ["a"]


def test_predicate_cancel_writes_the_host_event() -> None:
    from core.agent_harness.turns.host_cancel import bind_cancel_predicate

    sink = _CancelSink()
    cancelled = False

    def _is_cancelled() -> bool:
        return cancelled

    console = bind_cancel_predicate(sink, _is_cancelled)
    assert console.cancel_requested is False
    assert host_cancel_requested(sink) is False
    cancelled = True
    assert console.cancel_requested is True
    assert host_cancel_requested(sink) is True
