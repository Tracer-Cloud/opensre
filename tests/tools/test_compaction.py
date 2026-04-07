"""Tests for evidence compaction utilities."""

from __future__ import annotations

from app.tools.utils.compaction import (
    DEFAULT_LOG_LIMIT,
    DEFAULT_MESSAGE_CHARS,
    compact_invocations,
    compact_logs,
    compact_metrics,
    compact_traces,
    summarize_counts,
    truncate_list,
    truncate_log_entry,
    truncate_message,
)


class TestTruncateList:
    def test_truncate_list_with_explicit_limit(self) -> None:
        items = [1, 2, 3, 4, 5]
        result = truncate_list(items, limit=3)
        assert result == [1, 2, 3]

    def test_truncate_list_with_default_limit(self) -> None:
        items = list(range(100))
        result = truncate_list(items, limit=None)
        assert len(result) == DEFAULT_LOG_LIMIT

    def test_truncate_list_no_truncation_needed(self) -> None:
        items = [1, 2, 3]
        result = truncate_list(items, limit=10)
        assert result == [1, 2, 3]

    def test_truncate_list_empty(self) -> None:
        result = truncate_list([], limit=10)
        assert result == []


class TestTruncateMessage:
    def test_truncate_message_short(self) -> None:
        msg = "short message"
        result = truncate_message(msg, max_chars=100)
        assert result == msg

    def test_truncate_message_long(self) -> None:
        msg = "a" * 2000
        result = truncate_message(msg, max_chars=100)
        assert result.endswith("...")
        assert len(result) == 100

    def test_truncate_message_exact_limit(self) -> None:
        msg = "a" * 100
        result = truncate_message(msg, max_chars=100)
        assert result == msg


class TestTruncateLogEntry:
    def test_truncate_log_entry_message(self) -> None:
        log = {"message": "a" * 2000, "timestamp": "2024-01-01"}
        result = truncate_log_entry(log)
        assert result["message"].endswith("...")
        assert len(result["message"]) == DEFAULT_MESSAGE_CHARS
        assert result["timestamp"] == "2024-01-01"

    def test_truncate_log_entry_no_message(self) -> None:
        log = {"timestamp": "2024-01-01", "level": "INFO"}
        result = truncate_log_entry(log)
        assert result == log

    def test_truncate_log_entry_non_dict(self) -> None:
        log = "not a dict"
        result = truncate_log_entry(log)  # type: ignore
        assert result == log


class TestCompactLogs:
    def test_compact_logs_truncates_list(self) -> None:
        logs = [{"message": f"log {i}"} for i in range(100)]
        result = compact_logs(logs, limit=10)
        assert len(result) == 10

    def test_compact_logs_truncates_messages(self) -> None:
        logs = [{"message": "a" * 5000}]
        result = compact_logs(logs)
        assert result[0]["message"].endswith("...")

    def test_compact_logs_empty(self) -> None:
        result = compact_logs([])
        assert result == []


class TestCompactTraces:
    def test_compact_traces_truncates_list(self) -> None:
        traces = [{"id": i} for i in range(50)]
        result = compact_traces(traces, limit=10)
        assert len(result) == 10

    def test_compact_traces_limits_spans(self) -> None:
        trace = {"id": 1, "spans": [{"name": f"span {i}"} for i in range(100)]}
        result = compact_traces([trace], limit=1, max_spans_per_trace=20)
        assert len(result[0]["spans"]) == 20
        assert result[0].get("span_count_total") == 100

    def test_compact_traces_no_spans(self) -> None:
        trace = {"id": 1}
        result = compact_traces([trace], limit=1)
        assert result[0] == trace


class TestCompactMetrics:
    def test_compact_metrics_truncates_list(self) -> None:
        metrics = [{"name": f"metric {i}"} for i in range(100)]
        result = compact_metrics(metrics, limit=10)
        assert len(result) == 10

    def test_compact_metrics_limits_datapoints(self) -> None:
        metric = {
            "name": "cpu",
            "datapoints": [{"value": i} for i in range(100)],
        }
        result = compact_metrics([metric], limit=1, max_datapoints=20)
        assert len(result[0]["datapoints"]) == 20
        assert result[0].get("datapoints_total") == 100

    def test_compact_metrics_limits_values(self) -> None:
        metric = {
            "name": "cpu",
            "values": [{"value": i} for i in range(100)],
        }
        result = compact_metrics([metric], limit=1, max_datapoints=20)
        assert len(result[0]["values"]) == 20


class TestCompactInvocations:
    def test_compact_invocations_truncates_list(self) -> None:
        invocations = [{"request_id": f"req-{i}"} for i in range(100)]
        result = compact_invocations(invocations, limit=10)
        assert len(result) == 10

    def test_compact_invocations_limits_logs(self) -> None:
        invocation = {
            "request_id": "req-1",
            "logs": [{"message": f"log {i}"} for i in range(50)],
        }
        result = compact_invocations([invocation], limit=1, max_logs_per_invocation=10)
        assert len(result[0]["logs"]) == 10
        assert result[0].get("log_count_total") == 50


class TestSummarizeCounts:
    def test_summarize_counts_truncated(self) -> None:
        result = summarize_counts(100, 50, "logs")
        assert result == "Showing 50 of 100 logs"

    def test_summarize_counts_not_truncated(self) -> None:
        result = summarize_counts(50, 50, "logs")
        assert result is None

    def test_summarize_counts_less_than_limit(self) -> None:
        result = summarize_counts(30, 50, "logs")
        assert result is None


class TestConstants:
    def test_default_limits_are_reasonable(self) -> None:
        """Ensure default limits prevent prompt overflow while preserving useful data."""
        from app.tools.utils.compaction import (
            DEFAULT_ERROR_LOG_LIMIT,
            DEFAULT_LOG_LIMIT,
            DEFAULT_MESSAGE_CHARS,
            DEFAULT_METRICS_LIMIT,
            DEFAULT_TRACE_LIMIT,
        )

        assert DEFAULT_LOG_LIMIT <= 100  # Prevent overwhelming logs
        assert DEFAULT_ERROR_LOG_LIMIT <= DEFAULT_LOG_LIMIT  # Errors subset of logs
        assert DEFAULT_TRACE_LIMIT <= 50  # Traces are larger
        assert DEFAULT_METRICS_LIMIT <= 100  # Metrics can be numerous
        assert DEFAULT_MESSAGE_CHARS <= 2000  # Prevent huge log messages
