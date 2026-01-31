"""Data source detection for dynamic investigation.

Scans alert annotations and state context to detect available data sources
(CloudWatch, S3, local files, Tracer Web) and extract their parameters.
"""

from typing import Any


def detect_sources(raw_alert: dict[str, Any] | str, context: dict[str, Any]) -> dict[str, dict]:
    """
    Detect relevant data sources from alert annotations and context.

    Scans multiple locations for source information:
    - raw_alert.annotations
    - raw_alert.commonAnnotations
    - raw_alert top-level fields
    - context (for Tracer Web)

    Args:
        raw_alert: Raw alert payload (dict or str)
        context: Investigation context dictionary

    Returns:
        Dictionary mapping source type to extracted parameters:
        {
          "cloudwatch": {"log_group": "...", "log_stream": "...", "region": "..."},
          "s3": {"bucket": "...", "prefix": "...", "key": "..."},
          "local_file": {"log_file": "...", "log_path": "..."},
          "tracer_web": {"trace_id": "...", "run_url": "..."},
          "lambda": {"function_name": "..."}
        }
    """
    sources: dict[str, dict] = {}

    if isinstance(raw_alert, str):
        raw_alert = {}

    # Extract annotations from multiple possible locations
    annotations = {}
    if isinstance(raw_alert, dict):
        annotations = (
            raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {}) or {}
        )
        # Also check top-level fields
        if not annotations:
            annotations = raw_alert

    # Detect CloudWatch sources
    cloudwatch_log_group = (
        annotations.get("cloudwatch_log_group")
        or annotations.get("log_group")
        or annotations.get("cloudwatchLogGroup")
        or annotations.get("lambda_log_group")  # Lambda-specific alias
    )
    cloudwatch_log_stream = (
        annotations.get("cloudwatch_log_stream")
        or annotations.get("log_stream")
        or annotations.get("cloudwatchLogStream")
    )

    if cloudwatch_log_group:
        cloudwatch_params: dict[str, str] = {
            "log_group": cloudwatch_log_group,
            "region": (
                annotations.get("cloudwatch_region")
                or annotations.get("aws_region")
                or annotations.get("region")
                or "us-east-1"
            ),
        }
        if cloudwatch_log_stream:
            cloudwatch_params["log_stream"] = cloudwatch_log_stream
        sources["cloudwatch"] = cloudwatch_params

    # Detect S3 sources
    s3_bucket = (
        annotations.get("s3_bucket") or annotations.get("bucket") or annotations.get("s3Bucket")
    )
    s3_prefix = (
        annotations.get("s3_prefix") or annotations.get("prefix") or annotations.get("s3Prefix")
    )
    s3_key = annotations.get("s3_key") or annotations.get("key") or annotations.get("s3Key")

    if s3_bucket:
        s3_params: dict[str, str] = {"bucket": s3_bucket}
        if s3_prefix:
            s3_params["prefix"] = s3_prefix
        if s3_key:
            s3_params["key"] = s3_key
        sources["s3"] = s3_params

    # Detect local file sources
    log_file = (
        annotations.get("log_file") or annotations.get("log_path") or annotations.get("logFile")
    )
    if log_file:
        sources["local_file"] = {"log_file": log_file}

    # Detect Lambda sources
    # Look for function_name in various annotation patterns
    lambda_function = None
    for key in annotations:
        if key in ("function_name", "lambda_function"):
            lambda_function = annotations[key]
            break
        # Check for patterns like mock_dag_function, ingester_function, etc.
        if key.endswith("_function") and annotations[key]:
            lambda_function = annotations[key]
            break

    if lambda_function:
        sources["lambda"] = {"function_name": lambda_function}

    # Detect Tracer Web sources from context
    tracer_web_run = context.get("tracer_web_run", {})
    if isinstance(tracer_web_run, dict) and tracer_web_run.get("trace_id"):
        tracer_params: dict[str, str] = {"trace_id": tracer_web_run["trace_id"]}
        if tracer_web_run.get("run_url"):
            tracer_params["run_url"] = tracer_web_run["run_url"]
        sources["tracer_web"] = tracer_params

    return sources
