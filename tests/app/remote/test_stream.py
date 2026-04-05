"""Tests for the SSE stream parser."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.remote.stream import _build_event, _extract_node_name, parse_sse_stream


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


class TestExtractNodeName:
    def test_single_top_level_key(self) -> None:
        assert _extract_node_name("updates", {"investigate": {"evidence": {}}}) == "investigate"

    def test_multiple_keys_no_match(self) -> None:
        assert _extract_node_name("updates", {"a": 1, "b": 2}) == ""

    def test_ignores_dunder_keys(self) -> None:
        assert _extract_node_name("updates", {"__metadata": {}, "diagnose": {}}) == "diagnose"

    def test_name_field(self) -> None:
        assert _extract_node_name("events", {"name": "plan_actions"}) == "plan_actions"

    def test_metadata_langgraph_node(self) -> None:
        data = {"metadata": {"langgraph_node": "publish"}, "other": "stuff"}
        assert _extract_node_name("events", data) == "publish"

    def test_non_dict_data(self) -> None:
        assert _extract_node_name("updates", "not a dict") == ""  # type: ignore[arg-type]


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
