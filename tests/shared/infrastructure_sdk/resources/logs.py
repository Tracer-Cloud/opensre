"""CloudWatch log groups management."""

from typing import Any

from botocore.exceptions import ClientError

from tests.shared.infrastructure_sdk.deployer import (
    DEFAULT_REGION,
    get_boto3_client,
    get_standard_tags_dict,
)


def create_log_group(
    name: str,
    retention_days: int = 7,
    stack_name: str | None = None,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Create log group.

    Args:
        name: Log group name.
        retention_days: Log retention in days (1, 3, 5, 7, 14, 30, 60, 90, etc.).
        stack_name: Stack name for tagging.
        region: AWS region.

    Returns:
        Dictionary with log group info: name, arn.
    """
    logs_client = get_boto3_client("logs", region)

    try:
        logs_client.create_log_group(logGroupName=name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            raise

    # Set retention
    logs_client.put_retention_policy(
        logGroupName=name,
        retentionInDays=retention_days,
    )

    # Add tags
    if stack_name:
        logs_client.tag_log_group(
            logGroupName=name,
            tags=get_standard_tags_dict(stack_name),
        )

    # Get ARN
    response = logs_client.describe_log_groups(logGroupNamePrefix=name, limit=1)
    arn = (
        response["logGroups"][0]["arn"]
        if response["logGroups"]
        else f"arn:aws:logs:{region}:*:log-group:{name}"
    )

    return {
        "name": name,
        "arn": arn,
    }


def delete_log_group(name: str, region: str = DEFAULT_REGION) -> None:
    """Delete log group.

    Args:
        name: Log group name.
        region: AWS region.
    """
    logs_client = get_boto3_client("logs", region)

    try:
        logs_client.delete_log_group(logGroupName=name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise


def get_log_group(name: str, region: str = DEFAULT_REGION) -> dict[str, Any] | None:
    """Get log group details.

    Args:
        name: Log group name.
        region: AWS region.

    Returns:
        Log group details or None if not found.
    """
    logs_client = get_boto3_client("logs", region)

    try:
        response = logs_client.describe_log_groups(logGroupNamePrefix=name, limit=1)
        for group in response.get("logGroups", []):
            if group["logGroupName"] == name:
                return {
                    "name": group["logGroupName"],
                    "arn": group["arn"],
                    "retention_days": group.get("retentionInDays"),
                    "stored_bytes": group.get("storedBytes", 0),
                }
        return None
    except ClientError:
        return None


def get_recent_logs(
    log_group_name: str,
    log_stream_name: str | None = None,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
    limit: int = 100,
    region: str = DEFAULT_REGION,
) -> list[dict[str, Any]]:
    """Get recent log events.

    Args:
        log_group_name: Log group name.
        log_stream_name: Optional log stream name filter.
        start_time_ms: Start time in milliseconds since epoch.
        end_time_ms: End time in milliseconds since epoch.
        limit: Maximum number of events to return.
        region: AWS region.

    Returns:
        List of log events with timestamp, message, and stream name.
    """
    logs_client = get_boto3_client("logs", region)

    params: dict[str, Any] = {
        "logGroupName": log_group_name,
        "limit": limit,
        "interleaved": True,
    }

    if log_stream_name:
        params["logStreamNames"] = [log_stream_name]

    if start_time_ms:
        params["startTime"] = start_time_ms

    if end_time_ms:
        params["endTime"] = end_time_ms

    try:
        response = logs_client.filter_log_events(**params)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return []
        raise

    return [
        {
            "timestamp": event["timestamp"],
            "message": event["message"],
            "log_stream": event.get("logStreamName"),
        }
        for event in response.get("events", [])
    ]


def put_log_events(
    log_group_name: str,
    log_stream_name: str,
    messages: list[str],
    region: str = DEFAULT_REGION,
) -> None:
    """Write log events to a stream.

    Args:
        log_group_name: Log group name.
        log_stream_name: Log stream name (created if doesn't exist).
        messages: List of log messages.
        region: AWS region.
    """
    import time

    logs_client = get_boto3_client("logs", region)

    # Ensure stream exists
    try:
        logs_client.create_log_stream(
            logGroupName=log_group_name,
            logStreamName=log_stream_name,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            raise

    # Put events
    timestamp = int(time.time() * 1000)
    events = [{"timestamp": timestamp + i, "message": msg} for i, msg in enumerate(messages)]

    logs_client.put_log_events(
        logGroupName=log_group_name,
        logStreamName=log_stream_name,
        logEvents=events,
    )


def list_log_streams(
    log_group_name: str,
    prefix: str | None = None,
    limit: int = 50,
    region: str = DEFAULT_REGION,
) -> list[dict[str, Any]]:
    """List log streams in a group.

    Args:
        log_group_name: Log group name.
        prefix: Optional stream name prefix filter.
        limit: Maximum streams to return.
        region: AWS region.

    Returns:
        List of log stream details.
    """
    logs_client = get_boto3_client("logs", region)

    params: dict[str, Any] = {
        "logGroupName": log_group_name,
        "orderBy": "LastEventTime",
        "descending": True,
        "limit": limit,
    }

    if prefix:
        params["logStreamNamePrefix"] = prefix

    try:
        response = logs_client.describe_log_streams(**params)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return []
        raise

    return [
        {
            "name": stream["logStreamName"],
            "first_event_timestamp": stream.get("firstEventTimestamp"),
            "last_event_timestamp": stream.get("lastEventTimestamp"),
            "stored_bytes": stream.get("storedBytes", 0),
        }
        for stream in response.get("logStreams", [])
    ]
