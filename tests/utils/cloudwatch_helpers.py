"""
Pure functions for CloudWatch operations.
"""


def build_cloudwatch_console_url(
    log_group: str,
    log_stream: str,
    region: str = "us-east-1"
) -> str:
    """
    Build CloudWatch console URL for a specific log stream (stateless).

    Args:
        log_group: CloudWatch log group name
        log_stream: CloudWatch log stream name
        region: AWS region

    Returns:
        Full CloudWatch console URL
    """
    # URL-encode slashes for CloudWatch console
    encoded_group = log_group.replace("/", "$252F")
    encoded_stream = log_stream.replace("/", "$252F")

    return (
        f"https://{region}.console.aws.amazon.com/cloudwatch/home"
        f"?region={region}#logsV2:log-groups/log-group/{encoded_group}"
        f"/log-events/{encoded_stream}"
    )


def verify_logs_in_cloudwatch(
    cloudwatch_client,
    log_group: str,
    log_stream: str,
    limit: int = 1
) -> bool:
    """
    Verify logs are present in CloudWatch (stateless check).

    Args:
        cloudwatch_client: boto3 CloudWatch Logs client
        log_group: CloudWatch log group name
        log_stream: CloudWatch log stream name
        limit: Number of events to retrieve for verification

    Returns:
        True if logs are present, False otherwise
    """
    try:
        response = cloudwatch_client.get_log_events(
            logGroupName=log_group,
            logStreamName=log_stream,
            limit=limit
        )
        return len(response.get("events", [])) > 0
    except Exception:
        return False
