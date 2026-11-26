"""CloudWatch Logs investigation tool."""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from core.tool import SideEffectLevel, report_run_error
from core.tool_framework import tool
from integrations.aws.env import make_boto3_client

#: FilterLogEvents pages read per call; each page holds up to 1 MB / 10,000 events.
_MAX_FILTER_PAGES = 10


def _newest_matching_events(
    client: Any, log_group: str, filter_pattern: str, limit: int
) -> tuple[list[dict[str, Any]], bool]:
    """The newest ``limit`` matches of the last two hours, oldest first, and whether
    the scan stopped before the end of the window.

    FilterLogEvents returns events oldest first and may return an empty page
    while more matches remain behind ``nextToken``, so the window is paged
    through rather than read from its first page.
    """
    kwargs: dict[str, Any] = {
        "logGroupName": log_group,
        "filterPattern": filter_pattern,
        "startTime": int((time.time() - 7200) * 1000),
    }
    newest: deque[dict[str, Any]] = deque(maxlen=max(limit, 1))
    seen_tokens: set[str] = set()
    for _ in range(_MAX_FILTER_PAGES):
        response = client.filter_log_events(**kwargs)
        newest.extend(response.get("events", []))
        token = response.get("nextToken")
        if not token or token in seen_tokens:
            return list(newest), False
        seen_tokens.add(token)
        kwargs["nextToken"] = token
    return list(newest), True


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


_CW_LOGS_ANTI_EXAMPLES = (
    "Do not call without an absolute log_group path (e.g. /aws/lambda/checkout).",
    "Do not use for Kubernetes pod logs — use kubernetes_get_pod_logs or get_eks_pod_logs.",
    "Do not invent filter_pattern values; omit the filter when the pattern is unknown.",
)


@tool(
    name="get_cloudwatch_logs",
    side_effect_level=SideEffectLevel.READ_ONLY,
    display_name="CloudWatch",
    source="cloudwatch",
    description=(
        "Fetch recent log events from one AWS CloudWatch Logs group. "
        "Require the absolute log_group name; optionally narrow with log_stream "
        "or filter_pattern (correlation id / ERROR)."
    ),
    use_cases=[
        "Retrieving error tracebacks from a known CloudWatch log group",
        "Searching a Lambda/ECS/log group by correlation ID or error pattern",
        "Auto-discovering the newest stream when only the log group is known",
    ],
    anti_examples=list(_CW_LOGS_ANTI_EXAMPLES),
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "log_group": {
                "type": "string",
                "description": "Absolute CloudWatch log group name (e.g. /aws/lambda/api)",
            },
            "log_stream": {
                "type": "string",
                "description": "Log stream name (optional — auto-discovered if absent)",
            },
            "filter_pattern": {
                "type": "string",
                "description": "CloudWatch filter pattern (correlation_id, ERROR, etc.)",
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
        # The shared AWS client honours AWS_REGION like every other AWS tool; a bare
        # boto3 client would only read AWS_DEFAULT_REGION or the profile.
        client = make_boto3_client("logs")
        if client is None:
            return {"error": "boto3 not available", "log_group": log_group}

        scan_truncated = False
        if filter_pattern:
            events, scan_truncated = _newest_matching_events(
                client, log_group, filter_pattern, limit
            )
            if not events:
                return {
                    "found": False,
                    "log_group": log_group,
                    "filter_pattern": filter_pattern,
                    "message": (
                        f"No log events found matching pattern: {filter_pattern} "
                        f"in the first {_MAX_FILTER_PAGES} result pages; the search "
                        "stopped before the end of the 2-hour window."
                        if scan_truncated
                        else f"No log events found matching pattern: {filter_pattern}"
                    ),
                    "scan_truncated": scan_truncated,
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

        # Both APIs return events oldest first.
        log_messages = [event.get("message", "") for event in events]
        result: dict[str, Any] = {
            "found": True,
            "log_group": log_group,
            "event_count": len(events),
            "error_logs": log_messages,
            "latest_error": log_messages[-1] if log_messages else None,
        }
        if filter_pattern:
            result["filter_pattern"] = filter_pattern
            result["searched_all_streams"] = True
            result["scan_truncated"] = scan_truncated
        else:
            result["log_stream"] = log_stream
        return result

    except Exception as e:
        report_run_error(
            e,
            tool_name="get_cloudwatch_logs",
            source="cloudwatch",
            component="integrations.cloudwatch.tools.cloudwatch_logs_tool.tool",
            method="make_boto3_client('logs')",
            extras={
                "log_group": log_group,
                "log_stream": log_stream,
                "filter_pattern": filter_pattern,
            },
        )
        return {
            "error": str(e),
            "log_group": log_group,
            "log_stream": log_stream if log_stream else "auto-discovery",
        }
