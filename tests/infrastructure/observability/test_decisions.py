"""Decision tracing stays optional and cannot change an agent's outcome."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from infrastructure.observability.trace import decisions
from infrastructure.observability.trace.spans import (
    bind_session_trace,
    get_session_trace_store,
    set_session_trace_store,
)


def test_inactive_tracing_does_not_read_session_state() -> None:
    previous = get_session_trace_store()
    set_session_trace_store(None)
    state_reads: list[bool] = []

    def unexpected_state_read() -> dict[str, Any]:
        state_reads.append(True)
        return {}

    try:
        with bind_session_trace("inactive"):
            decisions.record_decision("test", attributes={}, context=unexpected_state_read)
    finally:
        set_session_trace_store(previous)
    assert state_reads == []


def test_trace_failure_does_not_escape_or_log_response_content(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=decisions.__name__)
    monkeypatch.setattr(decisions, "is_session_trace_active", lambda: True)
    emitted: list[dict[str, Any]] = []

    def broken_sink(**kwargs: Any) -> None:
        emitted.append(kwargs)
        raise OSError("disk unavailable")

    monkeypatch.setattr(decisions, "emit_span", broken_sink)
    with bind_session_trace("trace-failure"):
        decisions.record_decision("test", attributes={"final_text": "password=hunter2"})
    assert emitted[0]["attributes"]["final_text"] == "[REDACTED:password]"
    assert "hunter2" not in caplog.text
