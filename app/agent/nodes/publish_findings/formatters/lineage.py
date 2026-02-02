"""Data lineage flow formatting for RCA reports."""

import json
from typing import Any

from app.agent.nodes.publish_findings.context.models import ReportContext
from app.agent.nodes.publish_findings.formatters.base import format_slack_link
from app.agent.nodes.publish_findings.urls.aws import build_cloudwatch_url, build_s3_console_url


def _extract_annotations(raw_alert: dict) -> dict[str, Any]:
    """Extract annotations from raw alert."""
    if not isinstance(raw_alert, dict):
        return {}

    annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {}) or {}

    # Try first alert if no top-level annotations
    if not annotations and raw_alert.get("alerts"):
        first_alert = raw_alert.get("alerts", [{}])[0]
        if isinstance(first_alert, dict):
            annotations = first_alert.get("annotations", {}) or {}

    return annotations


def format_data_lineage_flow(ctx: ReportContext) -> str:
    """Format data lineage flow from evidence (upstream to downstream).

    Shows the complete data flow through the pipeline:
    1. External API (if applicable)
    2. Trigger Lambda
    3. S3 Landing (input data)
    4. Pipeline Execution (Prefect/Airflow/etc.)
    5. S3 Processed (output data)

    Args:
        ctx: Report context containing evidence and alert data

    Returns:
        Formatted data lineage section with URLs
    """
    evidence = ctx.get("evidence", {})
    raw_alert = ctx.get("raw_alert", {})
    annotations = _extract_annotations(raw_alert)

    flow_nodes = []
    region = ctx.get("cloudwatch_region") or "us-east-1"

    # 1. External API (from audit payload)
    s3_audit = evidence.get("s3_audit_payload", {})
    if s3_audit.get("found") and s3_audit.get("content"):
        try:
            audit_content = s3_audit.get("content")
            audit_data = (
                json.loads(audit_content) if isinstance(audit_content, str) else audit_content
            )
            external_api_url = audit_data.get("external_api_url")
            if external_api_url:
                flow_nodes.append(format_slack_link("External API", external_api_url))
        except (json.JSONDecodeError, TypeError):
            pass

    # 2. Trigger Lambda (from S3 metadata or Lambda evidence)
    lambda_func = evidence.get("lambda_function", {})
    if lambda_func.get("function_name"):
        function_name = lambda_func["function_name"]
        lambda_url = (
            f"https://{region}.console.aws.amazon.com/lambda/home"
            f"?region={region}#/functions/{function_name}?tab=code"
        )
        flow_nodes.append(format_slack_link("Trigger Lambda", lambda_url))

    # 3. S3 Landing (input data)
    s3_object = evidence.get("s3_object", {})
    if s3_object.get("found"):
        bucket = s3_object.get("bucket")
        key = s3_object.get("key")
        s3_url = build_s3_console_url(bucket, key, region)
        flow_nodes.append(format_slack_link("S3 Landing", s3_url))

    # 4. Pipeline Execution (Prefect/Airflow)
    cw_url = build_cloudwatch_url(ctx)
    if cw_url:
        pipeline_name = (
            annotations.get("airflow_dag")
            or annotations.get("dag_id")
            or annotations.get("prefect_flow")
            or "Pipeline Executor"
        )
        flow_nodes.append(format_slack_link(pipeline_name, cw_url))

    # 5. S3 Processed (output)
    processed_bucket = annotations.get("processed_bucket")
    if processed_bucket:
        flow_nodes.append(f"S3 Processed: s3://{processed_bucket}/ (missing)")

    if not flow_nodes:
        return ""

    lines = ["*Data Lineage Flow (Evidence-Based)*"]
    for i, node in enumerate(flow_nodes, 1):
        arrow = " → " if i < len(flow_nodes) else ""
        lines.append(f"{i}. {node}{arrow}")

    return "\n" + "\n".join(lines) + "\n"
