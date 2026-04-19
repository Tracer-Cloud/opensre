"""Evidence compaction utilities for high-volume log/trace tools.

Provides shared truncation and summarization to keep diagnosis prompts
within regression limits on noisy fixtures.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

# Default limits for high-volume tools
DEFAULT_LOG_LIMIT = 50
DEFAULT_ERROR_LOG_LIMIT = 30
DEFAULT_TRACE_LIMIT = 20
DEFAULT_METRICS_LIMIT = 50
DEFAULT_MESSAGE_CHARS = 1000  # Max characters per log message


T = TypeVar("T")


def truncate_list(  # noqa: UP047
    items: Sequence[T],
    limit: int | None = None,
    default_limit: int = DEFAULT_LOG_LIMIT,
) -> list[T]:
    """Truncate a list to the specified limit.

    Args:
        items: List of items to truncate
        limit: Explicit limit (uses default_limit if None)
        default_limit: Default limit to apply when limit is None

    Returns:
        Truncated list
    """
    effective_limit = limit if limit is not None else default_limit
    return list(items)[:effective_limit]


def truncate_message(message: str, max_chars: int = DEFAULT_MESSAGE_CHARS) -> str:
    """Truncate a message to max characters with ellipsis indicator.

    Args:
        message: Message string to truncate
        max_chars: Maximum characters allowed

    Returns:
        Truncated message with "..." suffix if truncated
    """
    if len(message) <= max_chars:
        return message
    return message[: max_chars - 3] + "..."


def truncate_log_entry(log: dict[str, Any], max_chars: int = DEFAULT_MESSAGE_CHARS) -> dict[str, Any]:
    """Truncate message fields in a log entry.

    Args:
        log: Log entry dict
        max_chars: Maximum characters for message field

    Returns:
        Log entry with truncated message
    """
    if not isinstance(log, dict):
        return log

    result = dict(log)
    if "message" in result and isinstance(result["message"], str):
        result["message"] = truncate_message(result["message"], max_chars)
    return result


def compact_logs(
    logs: Sequence[dict[str, Any]],
    limit: int | None = None,
    max_chars: int = DEFAULT_MESSAGE_CHARS,
) -> list[dict[str, Any]]:
    """Compact logs: truncate list and truncate each message.

    Args:
        logs: List of log entries
        limit: Maximum number of logs to return
        max_chars: Maximum characters per log message

    Returns:
        Compacted log list
    """
    truncated = truncate_list(logs, limit, DEFAULT_LOG_LIMIT)
    return [truncate_log_entry(log, max_chars) for log in truncated]


def compact_traces(
    traces: Sequence[dict[str, Any]],
    limit: int | None = None,
    max_spans_per_trace: int = 50,
) -> list[dict[str, Any]]:
    """Compact traces: truncate list and limit spans per trace.

    Args:
        traces: List of trace dictionaries
        limit: Maximum number of traces to return
        max_spans_per_trace: Maximum spans to include per trace

    Returns:
        Compacted trace list
    """
    truncated = truncate_list(traces, limit, DEFAULT_TRACE_LIMIT)
    result = []
    for trace in truncated:
        if not isinstance(trace, dict):
            result.append(trace)
            continue
        compacted = dict(trace)
        if "spans" in compacted and isinstance(compacted["spans"], list):
            compacted["spans"] = compacted["spans"][:max_spans_per_trace]
            # Add count if truncated
            if len(trace.get("spans", [])) > max_spans_per_trace:
                compacted["span_count_total"] = len(trace.get("spans", []))
        result.append(compacted)
    return result


def compact_metrics(
    metrics: Sequence[dict[str, Any]],
    limit: int | None = None,
    max_datapoints: int = 20,
) -> list[dict[str, Any]]:
    """Compact metrics: truncate list and datapoints per metric.

    Args:
        metrics: List of metric dictionaries
        limit: Maximum number of metrics to return
        max_datapoints: Maximum datapoints per metric

    Returns:
        Compacted metric list
    """
    truncated = truncate_list(metrics, limit, DEFAULT_METRICS_LIMIT)
    result = []
    for metric in truncated:
        if not isinstance(metric, dict):
            result.append(metric)
            continue
        compacted = dict(metric)
        # Truncate datapoints if present
        for key in ("datapoints", "values", "points", "data"):
            if key in compacted and isinstance(compacted[key], list) and len(compacted[key]) > max_datapoints:
                compacted[key] = compacted[key][:max_datapoints]
                compacted[f"{key}_total"] = len(metric.get(key, []))
        result.append(compacted)
    return result


def compact_invocations(
    invocations: Sequence[dict[str, Any]],
    limit: int | None = None,
    max_logs_per_invocation: int = 10,
) -> list[dict[str, Any]]:
    """Compact Lambda invocations: truncate list and logs per invocation.

    Args:
        invocations: List of invocation dictionaries
        limit: Maximum number of invocations
        max_logs_per_invocation: Maximum logs per invocation

    Returns:
        Compacted invocation list
    """
    truncated = truncate_list(invocations, limit, DEFAULT_LOG_LIMIT)
    result = []
    for inv in truncated:
        if not isinstance(inv, dict):
            result.append(inv)
            continue
        compacted = dict(inv)
        if "logs" in compacted and isinstance(compacted["logs"], list):
            original_count = len(compacted["logs"])
            compacted["logs"] = compacted["logs"][:max_logs_per_invocation]
            if original_count > max_logs_per_invocation:
                compacted["log_count_total"] = original_count
        result.append(compacted)
    return result


def summarize_counts(
    total: int,
    returned: int,
    item_name: str = "items",
) -> str | None:
    """Generate a summary string when items are truncated.

    Args:
        total: Total number of items available
        returned: Number of items being returned
        item_name: Name of the items (e.g., "logs", "traces")

    Returns:
        Summary string if truncation occurred, None otherwise
    """
    if total <= returned:
        return None
    return f"Showing {returned} of {total} {item_name}"
