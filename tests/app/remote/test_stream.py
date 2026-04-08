"""Tests for the SSE stream parser."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.remote.stream import (
    _build_event,
    _extract_event_details,
    _extract_node_name,
    parse_sse_stream,
)


class TestBuildEvent:
    def test_valid_json(self) -> None:
        event = _build_event("updates", '{"extract_alert": {"is_noise": false}}')
        assert event.event_type == "updates"
        assert event.data == {"extract_alert": {"is_noise": False}}
        assert event.node_name == "extract_alert"

    def test_invalid_json_falls_back_to_raw(self) -> None:
        event = _build_event("updates", "not-json")
        assert event.data == {"raw": "not-json"}
        assert event.node_name == "raw"

    def test_empty_data(self) -> None:
        event = _build_event("end", "")
        assert event.data == {}
        assert event.node_name == ""

    def test_timestamp_is_set(self) -> None:
        event = _build_event("updates", '{"plan_actions": {}}')
        assert event.timestamp > 0

    def test_events_mode_extracts_kind(self) -> None:
        data = {
            "event": "on_tool_start",
            "name": "query_datadog_logs",
            "run_id": "run-abc",
            "tags": ["graph:step:3"],
            "metadata": {"langgraph_node": "investigate"},
        }
        event = _build_event("events", json.dumps(data))
        assert event.event_type == "events"
        assert event.kind == "on_tool_start"
        assert event.run_id == "run-abc"
        assert event.tags == ["graph:step:3"]
        assert event.node_name == "investigate"

    def test_events_mode_no_kind_for_updates(self) -> None:
        event = _build_event("updates", '{"extract_alert": {}}')
        assert event.kind == ""
        assert event.run_id == ""
        assert event.tags == []


class TestExtractNodeName:
    def test_single_top_level_key(self) -> None:
        assert _extract_node_name("updates", {"investigate": {"evidence": {}}}) == "investigate"

    def test_multiple_keys_no_match(self) -> None:
        assert _extract_node_name("updates", {"a": 1, "b": 2}) == ""

    def test_ignores_dunder_keys(self) -> None:
        assert _extract_node_name("updates", {"__metadata": {}, "diagnose": {}}) == "diagnose"

    def test_metadata_langgraph_node_preferred(self) -> None:
        data = {"metadata": {"langgraph_node": "publish"}, "name": "other_name"}
        assert _extract_node_name("events", data) == "publish"

    def test_name_field_fallback(self) -> None:
        assert _extract_node_name("events", {"name": "plan_actions"}) == "plan_actions"

    def test_non_dict_data(self) -> None:
        assert _extract_node_name("updates", "not a dict") == ""  # type: ignore[arg-type]


class TestExtractEventDetails:
    def test_events_mode_full_payload(self) -> None:
        data = {
            "event": "on_tool_start",
            "run_id": "run-123",
            "tags": ["graph:step:1", "seq:step:2"],
        }
        kind, run_id, tags = _extract_event_details("events", data)
        assert kind == "on_tool_start"
        assert run_id == "run-123"
        assert tags == ["graph:step:1", "seq:step:2"]

    def test_non_events_mode_returns_empty(self) -> None:
        kind, run_id, tags = _extract_event_details("updates", {"x": 1})
        assert kind == ""
        assert run_id == ""
        assert tags == []

    def test_missing_optional_fields(self) -> None:
        kind, run_id, tags = _extract_event_details("events", {"event": "on_chain_start"})
        assert kind == "on_chain_start"
        assert run_id == ""
        assert tags == []


class TestParseSSEStream:
    def _make_response(self, lines: list[str]) -> MagicMock:
        resp = MagicMock()
        resp.iter_lines.return_value = iter(lines)
        return resp

    def test_single_event(self) -> None:
        resp = self._make_response([
            "event: updates",
            'data: {"extract_alert": {"alert_name": "test"}}',
            "",
        ])
        events = list(parse_sse_stream(resp))
        assert len(events) == 1
        assert events[0].event_type == "updates"
        assert events[0].node_name == "extract_alert"

    def test_multiple_events(self) -> None:
        resp = self._make_response([
            "event: metadata",
            'data: {"run_id": "abc"}',
            "",
            "event: updates",
            'data: {"plan_actions": {"planned_actions": ["query_logs"]}}',
            "",
            "event: end",
            "data: {}",
            "",
        ])
        events = list(parse_sse_stream(resp))
        assert len(events) == 3
        assert events[0].event_type == "metadata"
        assert events[1].node_name == "plan_actions"
        assert events[2].event_type == "end"

    def test_multiline_data(self) -> None:
        resp = self._make_response([
            "event: updates",
            'data: {"investigate":',
            'data:  {"evidence": "found"}}',
            "",
        ])
        events = list(parse_sse_stream(resp))
        assert len(events) == 1
        assert events[0].data == {"investigate": {"evidence": "found"}}

    def test_trailing_event_without_blank_line(self) -> None:
        resp = self._make_response([
            "event: end",
            "data: {}",
        ])
        events = list(parse_sse_stream(resp))
        assert len(events) == 1
        assert events[0].event_type == "end"

    def test_empty_stream(self) -> None:
        resp = self._make_response([])
        events = list(parse_sse_stream(resp))
        assert len(events) == 0

    def test_ignores_non_event_lines(self) -> None:
        resp = self._make_response([
            ": comment line",
            "event: updates",
            'data: {"x": {}}',
            "",
        ])
        events = list(parse_sse_stream(resp))
        assert len(events) == 1

    def test_events_mode_sse_frame(self) -> None:
        payload = {
            "event": "on_tool_start",
            "name": "query_datadog_logs",
            "run_id": "r-42",
            "tags": ["graph:step:3"],
            "metadata": {"langgraph_node": "investigate"},
            "data": {"input": {"query": "error"}},
        }
        resp = self._make_response([
            "event: events",
            f"data: {json.dumps(payload)}",
            "",
        ])
        events = list(parse_sse_stream(resp))
        assert len(events) == 1
        evt = events[0]
        assert evt.event_type == "events"
        assert evt.kind == "on_tool_start"
        assert evt.node_name == "investigate"
        assert evt.run_id == "r-42"
        assert "graph:step:3" in evt.tags
