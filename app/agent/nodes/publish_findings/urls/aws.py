"""AWS console URL generators for various resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote


def _encode_aws_path(path: str) -> str:
    """Encode paths for AWS console URLs.

    AWS console uses $252F instead of / in URL paths.
    """
    return path.replace("/", "$252F")


def build_cloudwatch_url(ctx: Mapping[str, Any]) -> str | None:
    """Build CloudWatch logs URL from context.

    Priority:
    1. Use pre-built URL from context if available
    2. Build URL from log group + stream if available
    3. Build URL from log group only if available
    4. Return None if insufficient data

    Args:
        ctx: Report context containing CloudWatch metadata

    Returns:
        CloudWatch console URL or None if cannot be constructed
    """
    # Check if URL already provided
    cw_url = ctx.get("cloudwatch_logs_url")
    if cw_url:
        return str(cw_url)

    # Extract components
    cw_group = ctx.get("cloudwatch_log_group")
    cw_stream = ctx.get("cloudwatch_log_stream")
    region = ctx.get("cloudwatch_region") or "us-east-1"

    if not cw_group:
        return None

    # Encode log group
    encoded_group = _encode_aws_path(cw_group)

    # Build URL with stream if available
    if cw_stream:
        encoded_stream = _encode_aws_path(cw_stream)
        return (
            f"https://{region}.console.aws.amazon.com/cloudwatch/home"
            f"?region={region}#logsV2:log-groups/log-group/{encoded_group}"
            f"/log-events/{encoded_stream}"
        )

    # Build URL with just log group
    return (
        f"https://{region}.console.aws.amazon.com/cloudwatch/home"
        f"?region={region}#logsV2:log-groups/log-group/{encoded_group}"
    )


def build_s3_console_url(bucket: str, key: str, region: str = "us-east-1") -> str:
    """Generate AWS S3 console URL for an object.

    Args:
        bucket: S3 bucket name
        key: S3 object key
        region: AWS region (defaults to us-east-1)

    Returns:
        S3 console URL for the specified object
    """
    # URL encode the key for use in query parameters
    encoded_key = quote(key, safe="")

    return (
        f"https://s3.console.aws.amazon.com/s3/object/{bucket}?region={region}&prefix={encoded_key}"
    )


def build_lambda_console_url(
    function_name: str, region: str = "us-east-1", tab: str = "code"
) -> str:
    """Generate AWS Lambda console URL for a function.

    Args:
        function_name: Lambda function name
        region: AWS region (defaults to us-east-1)
        tab: Console tab to display (code, monitoring, configuration, etc.)

    Returns:
        Lambda console URL for the specified function
    """
    return (
        f"https://{region}.console.aws.amazon.com/lambda/home"
        f"?region={region}#/functions/{function_name}?tab={tab}"
    )


def build_ecs_console_url(cluster: str, region: str = "us-east-1") -> str:
    """Generate AWS ECS console URL for a cluster.

    Args:
        cluster: ECS cluster name
        region: AWS region (defaults to us-east-1)

    Returns:
        ECS console URL for the specified cluster
    """
    return f"https://{region}.console.aws.amazon.com/ecs/v2/clusters/{cluster}?region={region}"


def build_batch_console_url(job_queue: str, region: str = "us-east-1") -> str:
    """Generate AWS Batch console URL for a job queue.

    Args:
        job_queue: Batch job queue name
        region: AWS region (defaults to us-east-1)

    Returns:
        Batch console URL for the specified job queue
    """
    return (
        f"https://{region}.console.aws.amazon.com/batch/home"
        f"?region={region}#queues/detail/{job_queue}"
    )


def build_grafana_explore_url(
    grafana_endpoint: str,
    query: str,
) -> str | None:
    """Build Grafana Explore URL for a Loki log query.

    Args:
        grafana_endpoint: Base Grafana URL (e.g. https://myorg.grafana.net)
        query: LogQL query string

    Returns:
        Grafana Explore URL or None if endpoint is missing
    """
    if not grafana_endpoint:
        return None
    base = grafana_endpoint.rstrip("/")
    encoded_query = quote(query, safe="")
    return f"{base}/explore?left=%7B%22datasource%22%3A%22loki%22%2C%22queries%22%3A%5B%7B%22expr%22%3A%22{encoded_query}%22%7D%5D%7D"


def build_datadog_logs_url(
    query: str,
    site: str = "datadoghq.com",
) -> str:
    """Build Datadog Logs Explorer URL for a log search query.

    Args:
        query: Datadog log search query
        site: Datadog site (e.g. datadoghq.com, datadoghq.eu)

    Returns:
        Datadog Logs Explorer URL
    """
    encoded_query = quote(query, safe="")
    return f"https://app.{site}/logs?query={encoded_query}"
