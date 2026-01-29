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


def get_cloudwatch_logs(log_group: str, log_stream: str, limit: int = 100) -> dict:
    """
    Fetch error logs from AWS CloudWatch Logs.

    Use this when the alert includes CloudWatch log details.
    Essential for investigating pipeline failures logged to CloudWatch.

    Useful for:
    - Retrieving error tracebacks from CloudWatch
    - Analyzing application-level errors
    - Investigating file not found errors
    - Understanding pipeline failure root causes

    Args:
        log_group: CloudWatch log group name
        log_stream: CloudWatch log stream name
        limit: Maximum number of log events to fetch

    Returns:
        Dictionary with log events (logs, event_count, latest_log)
    """
    if not log_group or not log_stream:
        return {"error": "log_group and log_stream are required"}

    try:
        client = boto3.client("logs")

        response = client.get_log_events(
            logGroupName=log_group,
            logStreamName=log_stream,
            limit=limit,
            startFromHead=False
        )

        events = response.get("events", [])

        if not events:
            return {
                "found": False,
                "log_group": log_group,
                "log_stream": log_stream,
                "message": "No log events found"
            }

        log_messages = [event.get("message", "") for event in events]

        return {
            "found": True,
            "log_group": log_group,
            "log_stream": log_stream,
            "event_count": len(events),
            "error_logs": log_messages,
            "latest_error": log_messages[0] if log_messages else None,
        }

    except Exception as e:
        return {
            "error": str(e),
            "log_group": log_group,
            "log_stream": log_stream,
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
