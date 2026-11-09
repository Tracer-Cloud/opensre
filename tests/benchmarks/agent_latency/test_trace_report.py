"""The latency report counts model round trips the way a session log records them."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.benchmarks.agent_latency.trace_report import (
    REPORT_SCHEMA_VERSION,
    recent_session_logs,
    report_payload,
    summarize_sessions,
)


def _span(kind: str, name: str, duration_ms: int, **attributes: Any) -> str:
    return json.dumps(
        {
            "type": "trace_span",
            "span_kind": kind,
            "name": name,
            "duration_ms": duration_ms,
            "attributes": attributes,
        }
    )


def _model_call(duration_ms: int, tool_call_count: int, **attributes: Any) -> str:
    usage = {"input_tokens": 12_000, "tool_call_count": tool_call_count, **attributes}
    return _span("llm", "test-model", duration_ms, **usage)


def test_report_counts_round_trips_batching_bookkeeping_and_stalls(tmp_path: Path) -> None:
    # Arrange: spans in closing order, as the shell writes them.
    log = tmp_path / "session.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "id": "s1"}),
                # A scripted slash turn: no model behind it, so not a turn here.
                _span("llm", "invoke", 0, tool_call_count=1),
                _span("tool", "slash_invoke", 900),
                _span("loop", "react_loop", 1_000),
                # A working turn: a lone plan write, a batch of three reads, the answer.
                _model_call(4_000, 1),
                _span("tool", "update_plan", 1),
                _span("loop_iteration", "react_iteration", 4_010),
                _model_call(5_000, 3, first_event_ms=800),
                _span("tool", "github_cli", 700),
                _span("tool", "github_cli", 650),
                _span("tool", "github_cli", 720),
                _span("loop_iteration", "react_iteration", 5_800),
                _model_call(2_000, 0, first_event_ms=600),
                _span("loop_iteration", "react_iteration", 2_010),
                _span("loop", "react_loop", 12_000),
                # A request that never answered: no usage after two minutes.
                _span("llm", "test-model", 120_000),
                _span("loop", "react_loop", 120_000),
                '{"type": "trace_span", "span_kind": "llm", "name": "test-mo',
                # The process died here, mid-turn: the loop never closed.
                _model_call(3_000, 1),
                _span("tool", "update_plan", 1),
            ]
        ),
        encoding="utf-8",
    )

    # Act
    report = summarize_sessions(recent_session_logs(tmp_path, 10))

    # Assert
    assert (report.sessions, report.turns, report.model_calls) == (1, 2, 4)
    assert report.tool_calls_per_response == {0: 1, 1: 1, 3: 1}
    assert report.batched_response_share == 0.5
    assert report.bookkeeping_only_calls == 1
    assert report.stalled_calls == 1
    assert report.first_event_ms_p50 == 800
    assert report.model_calls_per_turn_p90 == 3
    assert report.model_time_share == round(131_000 / 132_000, 3)
    assert report_payload(report)["schema_version"] == REPORT_SCHEMA_VERSION


def test_a_directory_with_no_recorded_turn_reports_zeros(tmp_path: Path) -> None:
    report = summarize_sessions(recent_session_logs(tmp_path, 10))

    assert (report.sessions, report.turns, report.model_calls) == (0, 0, 0)
    assert report.model_time_share == 0.0
    assert report.first_event_ms_p50 is None
