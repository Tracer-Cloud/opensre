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
          "lambda": {"function_name": "...", "all_functions": [...]},
          "aws_metadata": {"ecs_cluster": "...", "vpc_id": "...", "region": "..."},
          "grafana": {"service_name": "...", "execution_run_id": "...", "connection_verified": bool}
        }

        The aws_metadata source contains ALL AWS-related annotations from the alert,
        enabling dynamic AWS SDK investigations (ECS, RDS, EC2, VPC, etc.).

        The grafana source is OPTIONAL and only added if service map shows Grafana connectivity.
    """
    sources: dict[str, dict] = {}

    if isinstance(raw_alert, str):
        raw_alert = {}

    # Extract annotations from multiple possible locations
    annotations: dict[str, Any] = {}
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

        # Add correlation_id for log filtering if available
        correlation_id = annotations.get("correlation_id") or annotations.get("correlationId")
        if correlation_id:
            cloudwatch_params["correlation_id"] = correlation_id

        sources["cloudwatch"] = cloudwatch_params

    # Detect S3 sources (landing bucket)
    s3_bucket = (
        annotations.get("s3_bucket")
        or annotations.get("bucket")
        or annotations.get("s3Bucket")
        or annotations.get("landing_bucket")
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

    # Detect S3 audit source (when audit_key is specified)
    audit_key = annotations.get("audit_key") or annotations.get("auditKey")
    if s3_bucket and audit_key:
        sources["s3_audit"] = {"bucket": s3_bucket, "key": audit_key}

    # Detect S3 processed bucket (for output verification)
    processed_bucket = annotations.get("processed_bucket") or annotations.get("processedBucket")
    processed_prefix = annotations.get("processed_prefix")
    if processed_bucket:
        processed_params: dict[str, str] = {"bucket": processed_bucket}
        if processed_prefix:
            processed_params["prefix"] = processed_prefix
        sources["s3_processed"] = processed_params

    # Detect local file sources
    log_file = (
        annotations.get("log_file") or annotations.get("log_path") or annotations.get("logFile")
    )
    if log_file:
        sources["local_file"] = {"log_file": log_file}

    # Detect Lambda sources
    # Collect all Lambda functions from annotations (primary + upstream/downstream)
    lambda_functions: list[str] = []
    for key in annotations:
        if key in ("function_name", "lambda_function") and annotations[key]:
            # Primary function (prioritize it)
            lambda_functions.insert(0, annotations[key])
        elif (
            key.endswith("_function")
            and annotations[key]
            and annotations[key] not in lambda_functions
        ):
            # Additional functions (ingester_function, mock_dag_function, etc.)
            lambda_functions.append(annotations[key])

    if lambda_functions:
        # Store primary function and additional functions
        sources["lambda"] = {
            "function_name": lambda_functions[0],  # Primary function for single-function actions
            "all_functions": lambda_functions,  # All functions for multi-function investigations
        }

    # Detect Tracer Web sources from context
    tracer_web_run = context.get("tracer_web_run", {})
    if isinstance(tracer_web_run, dict) and tracer_web_run.get("trace_id"):
        tracer_params: dict[str, str] = {"trace_id": tracer_web_run["trace_id"]}
        if tracer_web_run.get("run_url"):
            tracer_params["run_url"] = tracer_web_run["run_url"]
        sources["tracer_web"] = tracer_params

    # Collect ALL AWS-related metadata for dynamic AWS SDK investigations
    # This enables the agent to make intelligent AWS API calls based on alert context
    aws_metadata: dict[str, Any] = {}

    # Common AWS resource identifiers
    aws_patterns = [
        # ECS/Fargate
        "ecs_cluster",
        "ecs_service",
        "ecs_task",
        "ecs_task_arn",
        "task_definition",
        # RDS
        "db_instance",
        "db_instance_identifier",
        "db_cluster",
        # EC2
        "instance_id",
        "vpc_id",
        "subnet_id",
        "security_group",
        # Lambda (additional metadata beyond function_name)
        "lambda_arn",
        "lambda_alias",
        # S3 (additional buckets)
        "processed_bucket",
        "audit_bucket",
        # Step Functions
        "state_machine_arn",
        "execution_arn",
        # CloudFormation
        "stack_name",
        "stack_id",
        # General AWS
        "aws_account_id",
        "aws_region",
        "region",
    ]

    for key, value in annotations.items():
        # Collect fields matching AWS patterns
        if any(pattern in key.lower() for pattern in aws_patterns):
            if value and key not in aws_metadata:
                aws_metadata[key] = value
        # Also collect any field ending with common AWS suffixes
        elif value and any(
            key.endswith(suffix)
            for suffix in ("_arn", "_id", "_name", "_cluster", "_bucket", "_queue", "_topic")
        ):
            aws_metadata[key] = value

    # Add region for AWS SDK calls
    if "region" not in aws_metadata and "aws_region" not in aws_metadata:
        aws_metadata["region"] = (
            annotations.get("cloudwatch_region")
            or annotations.get("aws_region")
            or annotations.get("region")
            or "us-east-1"
        )

    if aws_metadata:
        sources["aws_metadata"] = aws_metadata

    # Detect Grafana sources (optional - only if service map shows connectivity)
    pipeline_name = annotations.get("pipeline_name") or context.get("pipeline_name", "")
    execution_run_id = (
        annotations.get("execution_run_id")
        or annotations.get("executionRunId")
        or annotations.get("correlation_id")
    )

    # Check for explicit Grafana account from alert annotations
    grafana_account_id = annotations.get("grafana_account") or annotations.get("grafana_account_id")

    if pipeline_name:
        from app.agent.tools.tool_actions.grafana_actions import check_grafana_connection

        connection_check = check_grafana_connection(pipeline_name, account_id=grafana_account_id)

        if connection_check.get("connected"):
            grafana_params: dict[str, Any] = {
                "service_name": connection_check["service_name"],
                "pipeline_name": pipeline_name,
                "connection_verified": True,
                "account_id": connection_check.get("account_id"),
            }

            if execution_run_id:
                grafana_params["execution_run_id"] = execution_run_id

            sources["grafana"] = grafana_params

    return sources
