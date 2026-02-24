"""Evidence availability checking for diagnosis."""

from typing import Any


def check_evidence_availability(
    context: dict[str, Any], evidence: dict[str, Any], raw_alert: dict | str
) -> tuple[bool, bool, bool]:
    """
    Check if sufficient evidence is available for diagnosis.

    Args:
        context: Investigation context
        evidence: Collected evidence
        raw_alert: Raw alert payload

    Returns:
        Tuple of (has_tracer_evidence, has_cloudwatch_evidence, has_alert_evidence)
    """
    web_run = context.get("tracer_web_run", {})
    has_tracer_evidence = web_run.get("found")
    has_cloudwatch_evidence = bool(
        evidence.get("error_logs")
        or evidence.get("cloudwatch_logs")
        or evidence.get("grafana_logs")
        or evidence.get("grafana_error_logs")
        or evidence.get("grafana_traces")
        or evidence.get("grafana_metrics")
        or evidence.get("datadog_logs")
        or evidence.get("datadog_monitors")
        or evidence.get("datadog_events")
        or evidence.get("s3_object", {}).get("found")
        or evidence.get("s3_audit_payload", {}).get("found")
        or evidence.get("s3_marker")
        or evidence.get("lambda_function")
        or evidence.get("lambda_logs")
    )

    # Check for evidence in alert annotations or raw text
    has_alert_evidence = False
    if isinstance(raw_alert, str) and len(raw_alert) > 50:
        has_alert_evidence = True
    elif isinstance(raw_alert, dict):
        annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
        body = raw_alert.get("body", "") or raw_alert.get("text", "") or raw_alert.get("message", "")
        has_alert_evidence = bool(
            body
            or (annotations and any(
                annotations.get(k)
                for k in ("log_excerpt", "failed_steps", "error", "error_message", "cloudwatch_logs_url")
            ))
        )

    return has_tracer_evidence, has_cloudwatch_evidence, has_alert_evidence


def check_vendor_evidence_missing(evidence: dict[str, Any]) -> bool:
    """
    Check if vendor/external API evidence is missing.

    Critical for upstream/downstream tracing scenarios.

    Args:
        evidence: Collected evidence

    Returns:
        True if vendor evidence is missing
    """
    vendor_evidence_present = bool(
        evidence.get("vendor_audit_from_logs")  # Parsed from Lambda logs
        or (
            evidence.get("s3_audit_payload", {}).get("found")
            and evidence.get("s3_audit_payload", {}).get("content")
        )  # Actual audit payload fetched
    )
    return not vendor_evidence_present
