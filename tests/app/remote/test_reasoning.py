"""Tests for the reasoning-step mapping layer."""

from __future__ import annotations

from app.remote.reasoning import reasoning_text, tool_display_name


class TestToolDisplayName:
    def test_known_tool(self) -> None:
        assert tool_display_name("query_datadog_logs") == "Datadog logs"

    def test_known_tool_grafana(self) -> None:
        assert tool_display_name("query_grafana_logs") == "Grafana Loki"

    def test_unknown_tool_desnakes(self) -> None:
        assert tool_display_name("my_custom_tool") == "my custom tool"

    def test_cloudwatch(self) -> None:
        assert tool_display_name("get_cloudwatch_logs") == "CloudWatch"


class TestReasoningText:
    def test_tool_start(self) -> None:
        data = {"name": "query_datadog_logs"}
        result = reasoning_text("on_tool_start", data, "investigate")
        assert result == "calling Datadog logs"

    def test_tool_start_unknown_tool(self) -> None:
        data = {"name": "fetch_pagerduty_incidents"}
        result = reasoning_text("on_tool_start", data, "investigate")
        assert result == "calling fetch pagerduty incidents"

    def test_tool_start_no_name(self) -> None:
        result = reasoning_text("on_tool_start", {}, "investigate")
        assert result == "calling tool"

    def test_tool_end_with_output(self) -> None:
        data = {"name": "query_datadog_logs", "data": {"output": "42 log entries found"}}
        result = reasoning_text("on_tool_end", data, "investigate")
        assert result == "Datadog logs returned"

    def test_tool_end_empty_output(self) -> None:
        data = {"name": "get_error_logs", "data": {"output": ""}}
        result = reasoning_text("on_tool_end", data, "investigate")
        assert result == "error logs done"

    def test_chat_model_start_investigate(self) -> None:
        result = reasoning_text("on_chat_model_start", {}, "investigate")
        assert result == "querying"

    def test_chat_model_start_diagnose(self) -> None:
        result = reasoning_text("on_chat_model_start", {}, "diagnose_root_cause")
        assert result == "reasoning"

    def test_chat_model_start_unknown_node(self) -> None:
        result = reasoning_text("on_chat_model_start", {}, "unknown_node")
        assert result == "thinking"

    def test_chat_model_stream_with_content(self) -> None:
        data = {"data": {"chunk": {"content": "The root cause is a schema mismatch"}}}
        result = reasoning_text("on_chat_model_stream", data, "diagnose")
        assert result is not None
        assert "root cause" in result

    def test_chat_model_stream_empty_content(self) -> None:
        data = {"data": {"chunk": {"content": ""}}}
        result = reasoning_text("on_chat_model_stream", data, "diagnose")
        assert result is None

    def test_chat_model_stream_truncates_long_content(self) -> None:
        long_text = "x" * 100
        data = {"data": {"chunk": {"content": long_text}}}
        result = reasoning_text("on_chat_model_stream", data, "diagnose")
        assert result is not None
        assert len(result) <= 60

    def test_unknown_kind_returns_none(self) -> None:
        result = reasoning_text("on_retriever_start", {}, "investigate")
        assert result is None

    def test_chat_model_stream_string_chunk(self) -> None:
        data = {"data": {"chunk": "some text"}}
        result = reasoning_text("on_chat_model_stream", data, "diagnose")
        assert result == "some text"

    def test_chat_model_stream_whitespace_only(self) -> None:
        data = {"data": {"chunk": {"content": "   "}}}
        result = reasoning_text("on_chat_model_stream", data, "diagnose")
        assert result is None
