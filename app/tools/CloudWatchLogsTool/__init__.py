"""CloudWatch Logs investigation tool."""

from __future__ import annotations

import time
from typing import Any

import boto3

from app.tools.tool_decorator import tool


def _cloudwatch_logs_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("cloudwatch", {}).get("log_group"))


def _cloudwatch_logs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    cw = sources.get("cloudwatch", {})
    return {
        "log_group": cw.get("log_group"),
        "log_stream": cw.get("log_stream"),
        "filter_pattern": cw.get("correlation_id"),
        "limit": 100,
    }


@tool(
    name="get_cloudwatch_logs",
    source="cloudwatch",
    description="Fetch error logs from AWS CloudWatch Logs.",
    use_cases=[
        "Retrieving error tracebacks from CloudWatch",
        "Analyzing application-level errors",
        "Investigating file not found errors",
        "Understanding pipeline failure root causes",
        "Auto-discovering recent logs from ECS tasks, Lambda functions, etc.",
        "Searching for logs by correlation ID or error pattern",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "log_group": {"type": "string", "description": "CloudWatch log group name (required)"},
            "log_stream": {
                "type": "string",
                "description": "Log stream name (optional — auto-discovered if absent)",
            },
            "filter_pattern": {
                "type": "string",
                "description": "Pattern to filter logs (e.g., correlation_id, error text)",
            },
            "limit": {"type": "integer", "default": 100},
        },
        "required": ["log_group"],
    },
    is_available=_cloudwatch_logs_available,
    extract_params=_cloudwatch_logs_extract_params,
)
def get_cloudwatch_logs(
    log_group: str,
    log_stream: str | None = None,
    filter_pattern: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Fetch error logs from AWS CloudWatch Logs."""
    if not log_group:
        return {"error": "log_group is required"}

    try:
        client = boto3.client("logs")

        if filter_pattern:
            response = client.filter_log_events(
                logGroupName=log_group,
                filterPattern=filter_pattern,
                limit=limit,
                startTime=int((time.time() - 7200) * 1000),
            )
            events = response.get("events", [])
            if not events:
                return {
                    "found": False,
                    "log_group": log_group,
                    "filter_pattern": filter_pattern,
                    "message": f"No log events found matching pattern: {filter_pattern}",
                }
        else:
            if not log_stream:
                streams_response = client.describe_log_streams(
                    logGroupName=log_group, orderBy="LastEventTime", descending=True, limit=1
                )
                if not streams_response.get("logStreams"):
                    return {
                        "found": False,
                        "log_group": log_group,
                        "message": "No log streams found in log group",
                    }
                log_stream = streams_response["logStreams"][0]["logStreamName"]

            response = client.get_log_events(
                logGroupName=log_group, logStreamName=log_stream, limit=limit, startFromHead=False
            )
            events = response.get("events", [])

        if not events:
            return {
                "found": False,
                "log_group": log_group,
                "log_stream": log_stream if not filter_pattern else None,
                "filter_pattern": filter_pattern,
                "message": "No log events found",
            }

        log_messages = [event.get("message", "") for event in events]
        result: dict[str, Any] = {
            "found": True,
            "log_group": log_group,
            "event_count": len(events),
            "error_logs": log_messages,
            "latest_error": log_messages[0] if log_messages else None,
        }
        if filter_pattern:
            result["filter_pattern"] = filter_pattern
            result["searched_all_streams"] = True
        else:
            result["log_stream"] = log_stream
        return result

    except Exception as e:
        return {
            "error": str(e),
            "log_group": log_group,
            "log_stream": log_stream if log_stream else "auto-discovery",
        }
