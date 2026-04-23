"""Tests for the StreamRenderer."""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import patch

from app.remote.renderer import StreamRenderer, _canonical_node_name
from app.remote.stream import StreamEvent


def _make_event(
    event_type: str,
    node: str = "",
    data: dict | None = None,
    *,
    kind: str = "",
    tags: list[str] | None = None,
) -> StreamEvent:
    return StreamEvent(
        event_type=event_type,
        node_name=node,
        data=data or {},
        kind=kind,
        tags=tags or [],
    )


def _investigation_events() -> Iterator[StreamEvent]:
    """Simulate a minimal investigation stream (updates mode)."""
    yield _make_event("metadata", data={"run_id": "r-1"})
    yield _make_event(
        "updates",
        "extract_alert",
        {
            "extract_alert": {
                "alert_name": "test-alert",
                "pipeline_name": "etl",
                "severity": "critical",
            }
        },
    )
    yield _make_event(
        "updates",
        "resolve_integrations",
        {"resolve_integrations": {"resolved_integrations": {"grafana": {}}}},
    )
    yield _make_event(
        "updates",
        "plan_actions",
        {"plan_actions": {"planned_actions": ["query_grafana_logs"]}},
    )
    yield _make_event(
        "updates",
        "investigate",
        {"investigate": {"evidence": {"logs": "error found"}}},
    )
    yield _make_event(
        "updates",
        "diagnose",
        {"diagnose": {"root_cause": "Schema mismatch", "validity_score": 0.85}},
    )
    yield _make_event(
        "updates",
        "publish",
        {"publish": {"report": "Investigation complete."}},
    )
    yield _make_event("end")


def _events_mode_stream() -> Iterator[StreamEvent]:
    """Simulate an events-mode investigation stream with tool calls."""
    yield _make_event("metadata", data={"run_id": "r-3"})

    yield _make_event(
        "events",
        "extract_alert",
        {"name": "extract_alert", "data": {}, "metadata": {"langgraph_node": "extract_alert"}},
        kind="on_chain_start",
        tags=["graph:step:1"],
    )
    yield _make_event(
        "events",
        "extract_alert",
        {
            "name": "extract_alert",
            "data": {"output": {"alert_name": "test", "severity": "high"}},
            "metadata": {"langgraph_node": "extract_alert"},
        },
        kind="on_chain_end",
        tags=["graph:step:1"],
    )

    yield _make_event(
        "events",
        "investigate",
        {"name": "investigate", "data": {}, "metadata": {"langgraph_node": "investigate"}},
        kind="on_chain_start",
        tags=["graph:step:3"],
    )
    yield _make_event(
        "events",
        "investigate",
        {
            "name": "query_datadog_logs",
            "data": {"input": {"query": "error"}},
            "metadata": {"langgraph_node": "investigate"},
        },
        kind="on_tool_start",
        tags=[],
    )
    yield _make_event(
        "events",
        "investigate",
        {
            "name": "query_datadog_logs",
            "data": {"output": "42 entries"},
            "metadata": {"langgraph_node": "investigate"},
        },
        kind="on_tool_end",
        tags=[],
    )
    yield _make_event(
        "events",
        "investigate",
        {
            "name": "investigate",
            "data": {"output": {"root_cause": "Schema error"}},
            "metadata": {"langgraph_node": "investigate"},
        },
        kind="on_chain_end",
        tags=["graph:step:3"],
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


class TestStreamRendererUpdatesMode:
    """Tests for legacy updates-mode rendering (backward compat)."""

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
                "updates",
                "extract_alert",
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


class TestStreamRendererEventsMode:
    """Tests for events-mode rendering (fine-grained tool/LLM events)."""

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_tracks_nodes_from_events(self) -> None:
        renderer = StreamRenderer()
        renderer.render_stream(_events_mode_stream())

        assert "extract_alert" in renderer.node_names_seen
        assert "investigate" in renderer.node_names_seen
        assert renderer.stream_completed is True

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_merges_chain_end_output_into_state(self) -> None:
        renderer = StreamRenderer()
        renderer.render_stream(_events_mode_stream())
        state = renderer.final_state

        assert state.get("root_cause") == "Schema error"
        assert state.get("alert_name") == "test"

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_tool_events_count(self) -> None:
        renderer = StreamRenderer()
        renderer.render_stream(_events_mode_stream())
        assert renderer.events_received == 8

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_ignores_events_without_node(self) -> None:
        def nodeless_events() -> Iterator[StreamEvent]:
            yield _make_event(
                "events",
                "",
                {"event": "on_chain_start", "name": "RunnableSequence"},
                kind="on_chain_start",
            )
            yield _make_event("end")

        renderer = StreamRenderer()
        renderer.render_stream(nodeless_events())
        assert renderer.node_names_seen == []

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_is_graph_node_event_with_step_tag(self) -> None:
        evt = _make_event(
            "events",
            "investigate",
            {"name": "investigate"},
            kind="on_chain_start",
            tags=["graph:step:3"],
        )
        assert StreamRenderer._is_graph_node_event(evt) is True

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_is_graph_node_event_name_match(self) -> None:
        evt = _make_event(
            "events",
            "investigate",
            {"name": "investigate"},
            kind="on_chain_start",
        )
        assert StreamRenderer._is_graph_node_event(evt) is True

    @patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "text"})
    def test_sub_chain_not_graph_node(self) -> None:
        evt = _make_event(
            "events",
            "investigate",
            {"name": "RunnableSequence"},
            kind="on_chain_start",
            tags=["langsmith:hidden"],
        )
        assert StreamRenderer._is_graph_node_event(evt) is False
