"""Extract report context from investigation state."""

from typing import Any

from app.agent.nodes.publish_findings.context.models import ReportContext


def _safe_get(data: dict | None, *keys: str, default: Any = None) -> Any:
    """Safely navigate nested dictionaries."""
    if data is None:
        return default

    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default

    return current


def _extract_cloudwatch_info(
    raw_alert: dict,
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Extract CloudWatch metadata from alert.

    Returns: (cloudwatch_url, log_group, log_stream, region, alert_id)
    """
    if not isinstance(raw_alert, dict):
        return None, None, None, None, None

    # Try to get annotations from various locations
    annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
    if not annotations and raw_alert.get("alerts"):
        first_alert = raw_alert.get("alerts", [{}])[0]
        if isinstance(first_alert, dict):
            annotations = first_alert.get("annotations", {}) or {}

    # Extract CloudWatch URL
    cloudwatch_url = (
        raw_alert.get("cloudwatch_logs_url")
        or raw_alert.get("cloudwatch_url")
        or _safe_get(annotations, "cloudwatch_logs_url")
        or _safe_get(annotations, "cloudwatch_url")
    )

    # Extract log group and stream
    cloudwatch_group = raw_alert.get("cloudwatch_log_group") or _safe_get(
        annotations, "cloudwatch_log_group"
    )
    cloudwatch_stream = raw_alert.get("cloudwatch_log_stream") or _safe_get(
        annotations, "cloudwatch_log_stream"
    )

    # Extract region
    cloudwatch_region = raw_alert.get("cloudwatch_region") or _safe_get(
        annotations, "cloudwatch_region"
    )

    # Extract alert ID
    alert_id = raw_alert.get("alert_id")

    return cloudwatch_url, cloudwatch_group, cloudwatch_stream, cloudwatch_region, alert_id


def _filter_valid_claims(claims: list[dict]) -> list[dict]:
    """Filter out invalid or junk claims.

    Removes claims that:
    - Have empty claim text
    - Start with "NON_" prefix (artifacts)
    """
    return [
        c
        for c in claims
        if c.get("claim", "").strip() and not c.get("claim", "").strip().startswith("NON_")
    ]


def build_report_context(state: dict[str, Any]) -> ReportContext:
    """Extract data from state.context and state.evidence for report formatting.

    Args:
        state: Investigation state containing context, evidence, and analysis results

    Returns:
        ReportContext with all data needed for report generation

    Note:
        This function uses defensive access patterns to handle missing or malformed
        data gracefully. Missing fields will use sensible defaults rather than raising
        exceptions.
    """
    # Extract top-level state data
    context = state.get("context", {}) or {}
    evidence = state.get("evidence", {}) or {}
    raw_alert = state.get("raw_alert", {}) or {}

    # Extract nested structures
    web_run = context.get("tracer_web_run", {}) or {}
    batch = evidence.get("batch_jobs", {}) or {}
    s3 = evidence.get("s3", {}) or {}

    # Extract and filter claims
    validated_claims = _filter_valid_claims(state.get("validated_claims", []))
    non_validated_claims = state.get("non_validated_claims", [])

    # Extract CloudWatch metadata
    (
        cloudwatch_url,
        cloudwatch_group,
        cloudwatch_stream,
        cloudwatch_region,
        alert_id,
    ) = _extract_cloudwatch_info(raw_alert)

    # Build context dictionary
    return {
        # Core RCA results
        "pipeline_name": state.get("pipeline_name", "unknown"),
        "root_cause": state.get("root_cause", ""),
        "confidence": state.get("confidence", 0.0),
        "validated_claims": validated_claims,
        "non_validated_claims": non_validated_claims,
        "validity_score": state.get("validity_score", 0.0),
        # S3 verification
        "s3_marker_exists": s3.get("marker_exists", False),
        # Tracer web run metadata
        "tracer_run_status": web_run.get("status"),
        "tracer_run_name": web_run.get("run_name"),
        "tracer_pipeline_name": web_run.get("pipeline_name"),
        "tracer_run_cost": web_run.get("run_cost", 0),
        "tracer_max_ram_gb": web_run.get("max_ram_gb", 0),
        "tracer_user_email": web_run.get("user_email"),
        "tracer_team": web_run.get("team"),
        "tracer_instance_type": web_run.get("instance_type"),
        "tracer_failed_tasks": len(evidence.get("failed_jobs", [])),
        # AWS Batch metadata
        "batch_failure_reason": batch.get("failure_reason"),
        "batch_failed_jobs": batch.get("failed_jobs", 0),
        # CloudWatch metadata
        "cloudwatch_log_group": cloudwatch_group,
        "cloudwatch_log_stream": cloudwatch_stream,
        "cloudwatch_logs_url": cloudwatch_url,
        "cloudwatch_region": cloudwatch_region,
        "alert_id": alert_id,
        # Raw data for deeper inspection
        "evidence": evidence,
        "raw_alert": raw_alert,
    }
