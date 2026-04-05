"""CloudWatch client for metrics and logs - LangChain tool implementation."""

from typing import Any

from app.services.env import make_boto3_client, require_aws_credentials
from app.tools.tool_decorator import tool

try:
    from botocore.exceptions import ClientError
except ImportError:

    class ClientError(Exception):  # type: ignore[no-redef]
        """Stub when botocore is not installed; prevents over-broad except clauses."""


def _get_cloudwatch_client():
    return make_boto3_client("cloudwatch")


def _get_cloudwatch_logs_client():
    return make_boto3_client("logs")


def get_metric_statistics(
    namespace: str,
    metric_name: str,
    dimensions: list[dict[str, str]] | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    period: int = 300,
    statistics: list[str] | None = None,
) -> dict[str, Any]:
    """
    Get CloudWatch metric statistics for monitoring and investigation.

    Use this tool to retrieve AWS CloudWatch metrics like CPU utilization, memory usage,
    or custom application metrics. This is useful for investigating resource constraints
    or performance issues in AWS Batch jobs or other AWS services.

    Args:
        namespace: Metric namespace (e.g., "AWS/Batch", "AWS/ECS")
        metric_name: Name of the metric (e.g., "CPUUtilization", "MemoryUtilization")
        dimensions: List of dimension dicts (e.g., [{"Name": "JobQueue", "Value": "queue-name"}])
        start_time: Start time in ISO format string (e.g., "2024-01-01T00:00:00Z")
        end_time: End time in ISO format string (e.g., "2024-01-01T01:00:00Z")
        period: Period in seconds (default 300 = 5 minutes)
        statistics: List of statistics to retrieve (e.g., ["Average", "Maximum", "Minimum"])

    Returns:
        dict with metric data containing Datapoints or error info
    """
    client = _get_cloudwatch_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}
    credentials_error = require_aws_credentials(context="cloudwatch_client.get_metric_statistics")
    if credentials_error:
        return credentials_error

    if statistics is None:
        statistics = ["Average", "Maximum", "Minimum"]

    try:
        response = client.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=dimensions or [],
            StartTime=start_time,
            EndTime=end_time,
            Period=period,
            Statistics=statistics,
        )
        return {"success": True, "data": response}
    except ClientError as e:
        return {"success": False, "error": str(e)}


def filter_log_events(
    log_group_name: str,
    filter_pattern: str | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """
    Filter CloudWatch Logs events by pattern for error investigation.

    Use this tool to search CloudWatch Logs for specific error patterns, keywords,
    or structured log queries. This is essential for finding error messages and
    understanding failure causes in AWS services.

    Args:
        log_group_name: Name of the log group (e.g., "/aws/batch/job")
        filter_pattern: Filter pattern (e.g., "ERROR" or "{ $.eventType = \"ERROR\" }")
        start_time: Start time as Unix timestamp in milliseconds
        end_time: End time as Unix timestamp in milliseconds
        limit: Maximum number of events to return (default 100)

    Returns:
        dict with log events array or error info
    """
    client = _get_cloudwatch_logs_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}
    credentials_error = require_aws_credentials(context="cloudwatch_client.filter_log_events")
    if credentials_error:
        return credentials_error

    try:
        kwargs = {
            "logGroupName": log_group_name,
            "limit": limit,
        }
        if filter_pattern:
            kwargs["filterPattern"] = filter_pattern
        if start_time:
            kwargs["startTime"] = start_time
        if end_time:
            kwargs["endTime"] = end_time

        response = client.filter_log_events(**kwargs)
        return {"success": True, "data": response.get("events", [])}
    except ClientError as e:
        return {"success": False, "error": str(e)}


def get_log_events(
    log_group_name: str,
    log_stream_name: str,
    start_time: int | None = None,
    end_time: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """
    Get CloudWatch Logs events from a specific log stream.

    Use this tool to retrieve logs from a specific log stream when you know the
    exact stream name. This is useful for getting detailed logs from a specific
    AWS Batch job or ECS task.

    Args:
        log_group_name: Name of the log group (e.g., "/aws/batch/job")
        log_stream_name: Name of the log stream (e.g., "job-12345/container-name/abc123")
        start_time: Start time as Unix timestamp in milliseconds
        end_time: End time as Unix timestamp in milliseconds
        limit: Maximum number of events to return (default 100)

    Returns:
        dict with log events array or error info
    """
    client = _get_cloudwatch_logs_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}
    credentials_error = require_aws_credentials(context="cloudwatch_client.get_log_events")
    if credentials_error:
        return credentials_error

    try:
        kwargs = {
            "logGroupName": log_group_name,
            "logStreamName": log_stream_name,
            "limit": limit,
        }
        if start_time:
            kwargs["startTime"] = start_time
        if end_time:
            kwargs["endTime"] = end_time

        response = client.get_log_events(**kwargs)
        return {"success": True, "data": response.get("events", [])}
    except ClientError as e:
        return {"success": False, "error": str(e)}


# Create LangChain tools from the functions
# These can be used in LangGraph agents while the functions above remain callable
get_metric_statistics_tool = tool(get_metric_statistics)
filter_log_events_tool = tool(filter_log_events)
get_log_events_tool = tool(get_log_events)
