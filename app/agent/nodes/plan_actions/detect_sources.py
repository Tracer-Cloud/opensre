"""Data source detection for dynamic investigation.

Scans alert annotations and state context to detect available data sources
(CloudWatch, S3, local files, Tracer Web, Grafana) and extract their parameters.
"""

from datetime import UTC, datetime
from typing import Any

from app.agent.tools.tool_actions.grafana.grafana_actions import _map_pipeline_to_service_name


def _alert_time_range_minutes(raw_alert: dict[str, Any]) -> int:
    """Compute time_range_minutes to cover the alert window.

    Extracts startsAt from the alert payload (Alertmanager/Grafana format),
    calculates how many minutes ago the alert fired, and adds a 30-minute
    buffer so the query window starts before the alert and ends now.

    Falls back to 60 minutes if the timestamp is missing or unparseable.
    """
    starts_at: str | None = None

    # Alertmanager webhook: alerts[0].startsAt
    alerts = raw_alert.get("alerts", [])
    if alerts and isinstance(alerts, list):
        starts_at = alerts[0].get("startsAt")

    # Top-level field (some adapters flatten it)
    if not starts_at:
        starts_at = raw_alert.get("startsAt") or raw_alert.get("timestamp")

    # Annotations fallback
    if not starts_at:
        annotations = raw_alert.get("annotations") or raw_alert.get("commonAnnotations") or {}
        starts_at = annotations.get("timestamp")

    if not starts_at:
        return 60

    try:
        alert_time = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        # Zero-value sentinel from Alertmanager ("0001-01-01") — treat as no data
        if alert_time.year < 2000:
            return 60
        minutes_ago = int((datetime.now(UTC) - alert_time).total_seconds() / 60)
        # Window: from alert time - 5 min (pre-alert context) to now, minimum 60
        return max(60, minutes_ago + 35)
    except (ValueError, TypeError):
        return 60


def _match_service_name(pipeline_name: str, available_service_names: list[str]) -> str:
    """Return the best matching service name from the pre-fetched Loki label values.

    Normalizes separators for comparison so "my_pipeline" matches "my-pipeline".
    Falls back to the first available name if no match is found.
    """
    if not available_service_names:
        return pipeline_name

    normalized = pipeline_name.lower().replace("_", "-").replace(" ", "-")
    for name in available_service_names:
        candidate = name.lower().replace("_", "-").replace(" ", "-")
        if normalized and (normalized in candidate or candidate in normalized):
            return name

    return available_service_names[0]


def detect_sources(
    raw_alert: dict[str, Any] | str,
    context: dict[str, Any],
    resolved_integrations: dict[str, Any] | None = None,
) -> dict[str, dict]:
    """
    Detect relevant data sources from alert annotations and context.

    Scans multiple locations for source information:
    - raw_alert.annotations
    - raw_alert.commonAnnotations
    - raw_alert top-level fields
    - context (for Tracer Web)
    - resolved_integrations (for Grafana credentials)

    Args:
        raw_alert: Raw alert payload (dict or str)
        context: Investigation context dictionary
        resolved_integrations: Pre-resolved integration credentials from resolve_integrations node

    Returns:
        Dictionary mapping source type to extracted parameters.
    """
    sources: dict[str, dict] = {}

    if isinstance(raw_alert, str):
        raw_alert = {}

    # Compute time window that covers the alert (used for Datadog/Grafana queries)
    alert_time_range_minutes = _alert_time_range_minutes(raw_alert)

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

    # Detect Grafana sources from resolved_integrations
    pipeline_name = annotations.get("pipeline_name") or context.get("pipeline_name", "")
    execution_run_id = (
        annotations.get("execution_run_id")
        or annotations.get("executionRunId")
        or annotations.get("correlation_id")
    )

    if resolved_integrations and resolved_integrations.get("grafana"):
        grafana_int = resolved_integrations["grafana"]
        endpoint = grafana_int.get("endpoint", "")
        api_key = grafana_int.get("api_key", "")

        if endpoint and api_key:
            service_name = _map_pipeline_to_service_name(pipeline_name) if pipeline_name else ""

            # Use pre-fetched service names from build_context to find the real service name
            pre_context = context.get("grafana_pre_context", {})
            available_service_names: list[str] = pre_context.get("service_names", [])
            if available_service_names:
                service_name = _match_service_name(service_name, available_service_names)

            grafana_params: dict[str, Any] = {
                "service_name": service_name,
                "pipeline_name": pipeline_name,
                "connection_verified": True,
                "grafana_endpoint": endpoint,
                "grafana_api_key": api_key,
                "available_service_names": available_service_names,
                "time_range_minutes": alert_time_range_minutes,
            }
            if execution_run_id:
                grafana_params["execution_run_id"] = execution_run_id
            sources["grafana"] = grafana_params

    # Detect Datadog sources from resolved_integrations
    if resolved_integrations and resolved_integrations.get("datadog"):
        dd_int = resolved_integrations["datadog"]
        dd_api_key = dd_int.get("api_key", "")
        dd_app_key = dd_int.get("app_key", "")

        if dd_api_key and dd_app_key:
            # Build a default log query from alert context
            kube_namespace = annotations.get("kube_namespace", "")
            alert_query = annotations.get("query", "")
            default_query = alert_query or (
                f"PIPELINE_ERROR kube_namespace:{kube_namespace}" if kube_namespace else pipeline_name or "*"
            )

            dd_params: dict[str, Any] = {
                "api_key": dd_api_key,
                "app_key": dd_app_key,
                "site": dd_int.get("site", "datadoghq.com"),
                "connection_verified": True,
                "pipeline_name": pipeline_name,
                "default_query": default_query,
                "monitor_query": f"tag:pipeline:{pipeline_name}" if pipeline_name else None,
                "time_range_minutes": alert_time_range_minutes,
            }

            # Include pre-fetched monitor context from build_context
            dd_pre_context = context.get("datadog_pre_context", {})
            if dd_pre_context.get("monitors"):
                dd_params["pre_fetched_monitors"] = dd_pre_context["monitors"]
                dd_params["pre_fetched_monitors_total"] = dd_pre_context.get("total", 0)

            kube_job = annotations.get("kube_job", "") or annotations.get("kube_job_name", "")
            kube_deployment = annotations.get("kube_deployment", "")
            if kube_namespace or kube_job:
                dd_params["kubernetes_context"] = {
                    k: v
                    for k, v in {
                        "namespace": kube_namespace,
                        "job": kube_job,
                        "deployment": kube_deployment,
                    }.items()
                    if v
                }

            sources["datadog"] = dd_params

    # Surface upstream pipeline failure from dependency_context (built by build_context)
    dep_ctx = context.get("dependency_context", {})
    if isinstance(dep_ctx, dict) and dep_ctx.get("causal_chain_detected"):
        upstream_pipelines = dep_ctx.get("upstream_pipelines", [])
        hint_parts = [
            f"{p['name']} failed {p.get('minutes_ago', '?')}min ago on shared asset {p.get('shared_asset', 'unknown')}"
            for p in upstream_pipelines
            if p.get("status") in ("failed", "error", "Failed", "Error")
        ]
        if hint_parts:
            sources["upstream_context"] = {
                "causal_chain_detected": True,
                "upstream_failure_hint": "; ".join(hint_parts),
                "upstream_pipelines": upstream_pipelines,
                "causal_chain_confidence": dep_ctx.get("causal_chain_confidence", 0.85),
            }

    return sources
