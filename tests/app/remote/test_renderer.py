"""Tests for the StreamRenderer."""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import patch

from app.remote.renderer import StreamRenderer, _canonical_node_name
from app.remote.stream import StreamEvent


def _make_event(event_type: str, node: str = "", data: dict | None = None) -> StreamEvent:
    return StreamEvent(event_type=event_type, node_name=node, data=data or {})


def _investigation_events() -> Iterator[StreamEvent]:
    """Simulate a minimal investigation stream."""
    yield _make_event("metadata", data={"run_id": "r-1"})
    yield _make_event(
        "updates", "extract_alert",
        {"extract_alert": {"alert_name": "test-alert", "pipeline_name": "etl", "severity": "critical"}},
    )
    yield _make_event(
        "updates", "resolve_integrations",
        {"resolve_integrations": {"resolved_integrations": {"grafana": {}}}},
    )
    yield _make_event(
        "updates", "plan_actions",
        {"plan_actions": {"planned_actions": ["query_grafana_logs"]}},
    )
    yield _make_event(
        "updates", "investigate",
        {"investigate": {"evidence": {"logs": "error found"}}},
    )
    yield _make_event(
        "updates", "diagnose",
        {"diagnose": {"root_cause": "Schema mismatch", "validity_score": 0.85}},
    )
    yield _make_event(
        "updates", "publish",
        {"publish": {"report": "Investigation complete."}},
    )
    yield _make_event("end")


class TestCanonicalNodeName:
    def test_diagnose_maps_to_diagnose_root_cause(self) -> None:
        assert _canonical_node_name("diagnose") == "diagnose_root_cause"

    def test_publish_maps_to_publish_findings(self) -> None:
        assert _canonical_node_name("publish") == "publish_findings"

    def test_extract_alert_unchanged(self) -> None:
        assert _canonical_node_name("extract_alert") == "extract_alert"

    def test_unknown_node_unchanged(self) -> None:
        assert _canonical_node_name("custom_node") == "custom_node"


class TestStreamRenderer:
    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_renders_full_investigation(self) -> None:
        renderer = StreamRenderer()
        final = renderer.render_stream(_investigation_events())

        assert renderer.events_received == 8
        assert "extract_alert" in renderer.node_names_seen
        assert "diagnose_root_cause" in renderer.node_names_seen
        assert "publish_findings" in renderer.node_names_seen
        assert final.get("root_cause") == "Schema mismatch"
        assert final.get("report") == "Investigation complete."
        assert renderer.stream_completed is True

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_accumulates_state(self) -> None:
        renderer = StreamRenderer()
        renderer.render_stream(_investigation_events())
        state = renderer.final_state

        assert state["alert_name"] == "test-alert"
        assert state["planned_actions"] == ["query_grafana_logs"]
        assert state["validity_score"] == 0.85

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_handles_empty_stream(self) -> None:
        renderer = StreamRenderer()
        final = renderer.render_stream(iter([]))

        assert renderer.events_received == 0
        assert renderer.node_names_seen == []
        assert final == {}

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_handles_noise_alert(self) -> None:
        def noise_events() -> Iterator[StreamEvent]:
            yield _make_event("metadata", data={"run_id": "r-2"})
            yield _make_event(
                "updates", "extract_alert",
                {"extract_alert": {"is_noise": True, "alert_name": "noise"}},
            )
            yield _make_event("end")

        renderer = StreamRenderer()
        final = renderer.render_stream(noise_events())

        assert final.get("is_noise") is True
        assert renderer.events_received == 3

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_node_message_for_plan_actions(self) -> None:
        renderer = StreamRenderer()
        renderer._final_state = {"planned_actions": ["query_logs", "get_metrics"]}
        msg = renderer._build_node_message("plan_actions")
        assert msg is not None
        assert "query_logs" in msg

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_node_message_for_diagnose(self) -> None:
        renderer = StreamRenderer()
        renderer._final_state = {"validity_score": 0.92}
        msg = renderer._build_node_message("diagnose_root_cause")
        assert msg is not None
        assert "92%" in msg
