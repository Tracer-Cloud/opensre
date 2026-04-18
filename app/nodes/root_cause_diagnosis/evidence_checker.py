"""Evidence availability checking for diagnosis."""

from typing import Any

# Alert state strings that indicate no active incident across common monitoring platforms.
_HEALTHY_STATES = frozenset({"normal", "resolved", "ok"})

# Severity levels that are non-actionable (i.e. scheduled checks, informational only).
_HEALTHY_SEVERITIES = frozenset({"info", "none", ""})

# Annotation keys whose non-empty presence signals an active error condition.
_ERROR_ANNOTATION_KEYS = ("error", "error_message", "log_excerpt", "failed_steps")

# Evidence keys whose presence (even with empty values) confirms investigation was attempted.
# An empty grafana_logs list is itself a healthy signal: no errors found during investigation.
_INVESTIGATED_EVIDENCE_KEYS = frozenset({
    "grafana_logs",
    "grafana_metrics",
    "grafana_alert_rules",
    "aws_cloudwatch_metrics",
    "aws_rds_events",
    "aws_performance_insights",
    "cloudwatch_logs",
    "datadog_logs",
    "datadog_monitors",
    "eks_pods",
    "eks_events",
    "eks_deployments",
    "eks_node_health",
    "eks_pod_logs",
    "eks_deployment_status",
})


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
        evidence.get("error_logs") is not None
        or evidence.get("cloudwatch_logs") is not None
        or evidence.get("grafana_logs") is not None
        or evidence.get("grafana_error_logs") is not None
        or evidence.get("grafana_traces") is not None
        or evidence.get("grafana_metrics") is not None
        or evidence.get("datadog_logs") is not None
        or evidence.get("datadog_monitors") is not None
        or evidence.get("datadog_events") is not None
        or evidence.get("s3_object", {}).get("found")
        or evidence.get("s3_audit_payload", {}).get("found")
        or evidence.get("s3_marker") is not None
        or evidence.get("lambda_function") is not None
        or evidence.get("lambda_logs") is not None
        or evidence.get("aws_cloudwatch_metrics") is not None
        or evidence.get("aws_rds_events") is not None
        or evidence.get("aws_performance_insights") is not None
        or evidence.get("eks_pods") is not None
        or evidence.get("eks_events") is not None
        or evidence.get("eks_node_health") is not None
        or evidence.get("eks_deployments") is not None
        or evidence.get("eks_pod_logs") is not None
        or evidence.get("eks_deployment_status") is not None
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


def is_clearly_healthy(raw_alert: dict[str, Any] | str, evidence: dict[str, Any]) -> bool:
    """Return True only when all four conditions confirm no active incident.

    Conditions (all must hold):
    1. Alert ``state`` is in {"normal", "resolved", "ok"} — covers Grafana, CloudWatch,
       PagerDuty, and most other monitoring platforms.
    2. Alert ``severity`` is in {"info", "none", ""} — rules out a resolved-critical that
       still warrants investigation.
    3. No error-signal annotation keys (``error``, ``error_message``, ``log_excerpt``,
       ``failed_steps``) are non-empty.
    4. At least one evidence key is populated — distinguishes "healthy evidence" from
       "no evidence gathered yet".

    Blast radius if this misfires (false-healthy): the short-circuit returns
    root_cause_category="healthy" without an LLM call. A real incident would receive a
    "healthy" report. This is mitigated by:
    - The severity gate: firing critical/high/warning alerts never satisfy condition 2.
    - The HEALTHY_SHORT_CIRCUIT env flag (default "true") — set to "false" to disable
      without a deploy.
    """
    if not isinstance(raw_alert, dict):
        return False

    # Condition 1: alert state signals no active incident.
    state = str(raw_alert.get("state", "")).lower().strip()
    if state not in _HEALTHY_STATES:
        return False

    # Condition 2: severity is non-actionable.
    labels = raw_alert.get("commonLabels", raw_alert.get("labels", {})) or {}
    severity = str(labels.get("severity", raw_alert.get("severity", ""))).lower().strip()
    if severity not in _HEALTHY_SEVERITIES:
        return False

    # Condition 3: no error-signal annotations.
    annotations = (
        raw_alert.get("commonAnnotations", raw_alert.get("annotations", {})) or {}
    )
    if any(annotations.get(key) for key in _ERROR_ANNOTATION_KEYS):
        return False

    # Condition 4: at least one known investigation key exists in evidence (even if empty).
    # An empty grafana_logs / grafana_metrics / etc. after a completed investigation is itself
    # a health signal — it means no errors were found. We only require that the key is present
    # (investigation was attempted), not that it contains data.
    return any(k in evidence for k in _INVESTIGATED_EVIDENCE_KEYS)


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
