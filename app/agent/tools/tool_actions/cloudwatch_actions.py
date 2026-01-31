"""
CloudWatch tool actions - LangChain tool implementation.

No printing, no LLM calls. Just fetch data and return typed results.
All functions are decorated with @tool for LangChain/LangGraph compatibility.
"""

try:
    from langchain.tools import tool
except ImportError:
    # Fallback if langchain not available - create a no-op decorator
    def tool(func=None, **kwargs):  # noqa: ARG001
        if func is None:
            return lambda f: f
        return func


import boto3

from app.agent.tools.clients.cloudwatch_client import get_metric_statistics


def get_cloudwatch_logs(
    log_group: str,
    log_stream: str | None = None,
    filter_pattern: str | None = None,
    limit: int = 100
) -> dict:
    """
    Fetch error logs from AWS CloudWatch Logs.

    Use this when the alert includes CloudWatch log details.
    Essential for investigating pipeline failures logged to CloudWatch.

    If log_stream is not provided, automatically discovers the most recent
    log stream in the log group. If filter_pattern is provided (e.g., a
    correlation_id), searches across all streams for matching logs.

    Useful for:
    - Retrieving error tracebacks from CloudWatch
    - Analyzing application-level errors
    - Investigating file not found errors
    - Understanding pipeline failure root causes
    - Auto-discovering recent logs from ECS tasks, Lambda functions, etc.
    - Searching for logs by correlation ID or error pattern

    Args:
        log_group: CloudWatch log group name (required)
        log_stream: CloudWatch log stream name (optional - will auto-discover if not provided)
        filter_pattern: Pattern to filter logs (e.g., correlation_id, error text)
        limit: Maximum number of log events to fetch

    Returns:
        Dictionary with log events (logs, event_count, latest_log)
    """
    if not log_group:
        return {"error": "log_group is required"}

    try:
        client = boto3.client("logs")

        # If filter_pattern is provided, use filter_log_events to search across all streams
        if filter_pattern:
            import time
            response = client.filter_log_events(
                logGroupName=log_group,
                filterPattern=filter_pattern,
                limit=limit,
                startTime=int((time.time() - 7200) * 1000),  # Last 2 hours
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
            # Auto-discover log stream if not provided
            if not log_stream:
                streams_response = client.describe_log_streams(
                    logGroupName=log_group,
                    orderBy="LastEventTime",
                    descending=True,
                    limit=1
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

        result = {
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


def get_cloudwatch_batch_metrics(job_queue: str, metric_type: str = "cpu") -> dict:
    """
    Get CloudWatch metrics for AWS Batch jobs.

    Useful for:
    - Proving resource constraint hypothesis
    - Understanding batch job performance
    - Identifying AWS infrastructure issues

    Args:
        job_queue: The AWS Batch job queue name
        metric_type: Either 'cpu' or 'memory'

    Returns:
        Dictionary with CloudWatch metrics
    """
    if not job_queue:
        return {"error": "job_queue is required"}

    if metric_type not in ["cpu", "memory"]:
        return {"error": "metric_type must be 'cpu' or 'memory'"}

    try:
        if metric_type == "cpu":
            metrics = get_metric_statistics(
                namespace="AWS/Batch",
                metric_name="CPUUtilization",
                dimensions=[{"Name": "JobQueue", "Value": job_queue}],
                statistics=["Average", "Maximum"],
            )
        else:
            metrics = get_metric_statistics(
                namespace="AWS/Batch",
                metric_name="MemoryUtilization",
                dimensions=[{"Name": "JobQueue", "Value": job_queue}],
                statistics=["Average", "Maximum"],
            )

        return {
            "metrics": metrics,
            "metric_type": metric_type,
            "job_queue": job_queue,
            "source": "AWS CloudWatch API",
        }
    except Exception as e:
        return {"error": f"CloudWatch not available: {str(e)}"}


# Create LangChain tool from the function
get_cloudwatch_batch_metrics_tool = tool(get_cloudwatch_batch_metrics)
