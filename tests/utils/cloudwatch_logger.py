"""
CloudWatch logging utilities (stateless).

Infrastructure code for logging to AWS CloudWatch.
"""

from datetime import UTC, datetime

import boto3

from tests.utils.cloudwatch_helpers import (
    build_cloudwatch_console_url,
    verify_logs_in_cloudwatch,
)


def send_to_cloudwatch(
    log_group: str,
    log_stream: str,
    message: str,
    region: str = "us-east-1",
) -> None:
    """Send log message to AWS CloudWatch Logs (stateless)."""
    from contextlib import suppress

    client = boto3.client("logs", region_name=region)

    with suppress(client.exceptions.ResourceAlreadyExistsException):
        client.create_log_group(logGroupName=log_group)

    with suppress(client.exceptions.ResourceAlreadyExistsException):
        client.create_log_stream(logGroupName=log_group, logStreamName=log_stream)

    timestamp_ms = int(datetime.now(UTC).timestamp() * 1000)
    client.put_log_events(
        logGroupName=log_group,
        logStreamName=log_stream,
        logEvents=[{"timestamp": timestamp_ms, "message": message}],
    )


def build_error_log_message(
    error_message: str,
    traceback_str: str,
    run_id: str,
    pipeline_name: str,
) -> str:
    """Build structured error log message (stateless)."""
    return f"""ERROR: {error_message}

Pipeline: {pipeline_name}
Run ID: {run_id}
Timestamp: {datetime.now(UTC).isoformat()}

Traceback:
{traceback_str}
"""


def log_error_to_cloudwatch(
    error: Exception,
    error_traceback: str,
    pipeline_name: str,
    run_id: str,
    test_name: str,
    region: str,
) -> dict:
    """
    Log error to CloudWatch (stateless).

    Args:
        error: The exception
        error_traceback: Full traceback
        pipeline_name: Pipeline name
        run_id: Run identifier
        test_name: Test/demo name (constructs log group: /tracer/ai-investigations/{test_name})
        region: AWS region

    Returns:
        Dict with log_group, log_stream, cloudwatch_url, error_message, logs_verified
    """
    error_message = str(error)
    log_stream = run_id
    log_group = f"/tracer/ai-investigations/{test_name}"

    log_message = build_error_log_message(
        error_message=error_message,
        traceback_str=error_traceback,
        run_id=run_id,
        pipeline_name=pipeline_name,
    )

    send_to_cloudwatch(log_group, log_stream, log_message, region)

    cw_client = boto3.client("logs", region_name=region)
    logs_present = verify_logs_in_cloudwatch(cw_client, log_group, log_stream)

    cloudwatch_url = build_cloudwatch_console_url(log_group, log_stream, region)

    return {
        "log_group": log_group,
        "log_stream": log_stream,
        "cloudwatch_url": cloudwatch_url,
        "error_message": error_message,
        "logs_verified": logs_present,
    }
