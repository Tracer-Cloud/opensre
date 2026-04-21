"""Lambda invocation logs from CloudWatch."""

from __future__ import annotations

from app.services.lambda_client import (
    get_invocation_logs_by_request_id,
    get_recent_invocations,
)
from app.tools.tool_decorator import tool
from app.tools.utils.compaction import compact_invocations, compact_logs, summarize_counts


def _lambda_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("lambda", {}).get("function_name"))


def _lambda_name(sources: dict[str, dict]) -> str:
    return str(sources.get("lambda", {}).get("function_name", ""))


def _extract_lambda_invocation_logs_params(sources: dict[str, dict]) -> dict:
    return {"function_name": _lambda_name(sources), "filter_errors": False, "limit": 50}


@tool(
    name="get_lambda_invocation_logs",
    source="cloudwatch",
    description="Get Lambda invocation logs from CloudWatch.",
    use_cases=[
        "Finding error messages and stack traces from Lambda executions",
        "Understanding data processing flow in Lambda functions",
        "Identifying issues with external API calls made by Lambda",
        "Tracing data transformation logic through log output",
    ],
    requires=["function_name"],
    input_schema={
        "type": "object",
        "properties": {
            "function_name": {"type": "string"},
            "request_id": {"type": "string"},
            "filter_errors": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "default": 50},
        },
        "required": ["function_name"],
    },
    is_available=_lambda_available,
    extract_params=_extract_lambda_invocation_logs_params,
)
def get_lambda_invocation_logs(
    function_name: str,
    request_id: str | None = None,
    filter_errors: bool = False,
    limit: int = 50,
) -> dict:
    """Get Lambda invocation logs from CloudWatch."""
    if not function_name:
        return {"error": "function_name is required"}

    if request_id:
        result = get_invocation_logs_by_request_id(function_name, request_id, limit)
        if not result.get("success"):
            return {
                "error": result.get("error", "Unknown error"),
                "function_name": function_name,
                "request_id": request_id,
            }
        data = result.get("data", {})
        logs = data.get("logs", [])
        # Compact logs to stay within prompt limits
        compacted_logs = compact_logs(logs, limit=limit)
        result_data = {
            "found": bool(compacted_logs),
            "function_name": function_name,
            "request_id": request_id,
            "log_group": data.get("log_group"),
            "event_count": data.get("event_count", 0),
            "logs": compacted_logs,
        }
        summary = summarize_counts(len(logs), len(compacted_logs), "logs")
        if summary:
            result_data["truncation_note"] = summary
        return result_data

    filter_pattern = "ERROR" if filter_errors else None
    result = get_recent_invocations(function_name, limit, filter_pattern)
    if not result.get("success"):
        return {"error": result.get("error", "Unknown error"), "function_name": function_name}

    data = result.get("data", {})
    invocations = data.get("invocations", [])
    all_logs = [
        {"request_id": inv.get("request_id"), "message": log}
        for inv in invocations
        for log in inv.get("logs", [])
    ]

    # Compact invocations and logs to stay within prompt limits
    compacted_invocations = compact_invocations(
        invocations, limit=limit, max_logs_per_invocation=10
    )
    compacted_recent_logs = compact_logs(all_logs, limit=20)

    # Build invocation summaries with limited log counts
    invocation_summaries = [
        {
            "request_id": inv.get("request_id"),
            "duration_ms": inv.get("duration_ms"),
            "memory_used_mb": inv.get("memory_used_mb"),
            "log_count": min(len(inv.get("logs", [])), 10),
        }
        for inv in compacted_invocations
    ]

    result_data = {
        "found": bool(compacted_invocations),
        "function_name": function_name,
        "log_group": data.get("log_group"),
        "invocation_count": data.get("invocation_count", 0),
        "invocations": invocation_summaries,
        "recent_logs": compacted_recent_logs,
    }
    summary = summarize_counts(len(invocations), len(compacted_invocations), "invocations")
    if summary:
        result_data["truncation_note"] = summary
    return result_data
