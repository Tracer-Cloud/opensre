"""Data models for report context."""

from typing import TypedDict


class ReportContext(TypedDict, total=False):
    """Data extracted from state for report formatting.

    Contains all information needed to generate the final RCA report,
    including pipeline metadata, root cause analysis results, validated claims,
    infrastructure assets, and evidence references.
    """

    # Core RCA results
    pipeline_name: str
    alert_name: str | None
    root_cause: str
    root_cause_category: str | None
    validated_claims: list[dict]
    non_validated_claims: list[dict]
    validity_score: float
    investigation_recommendations: list[str]
    remediation_steps: list[str]

    # S3 verification
    s3_marker_exists: bool

    # Tracer web run metadata
    tracer_run_status: str | None
    tracer_run_name: str | None
    tracer_pipeline_name: str | None
    tracer_run_cost: float
    tracer_max_ram_gb: float
    tracer_user_email: str | None
    tracer_team: str | None
    tracer_instance_type: str | None
    tracer_failed_tasks: int

    # AWS Batch metadata
    batch_failure_reason: str | None
    batch_failed_jobs: int

    # CloudWatch metadata
    cloudwatch_log_group: str | None
    cloudwatch_log_stream: str | None
    cloudwatch_logs_url: str | None
    cloudwatch_region: str | None
    alert_id: str | None
    evidence_catalog: dict
    investigation_duration_seconds: int | None

    # Raw data for deeper inspection
    evidence: dict  # Raw evidence data for citation
    raw_alert: dict  # Raw alert for infrastructure extraction

    # Upstream causal chain (from dependency context source)
    causal_chain: dict | None  # dependency_context dict, or None when absent

    # Tool call history for investigation transparency
    executed_hypotheses: list[dict]

    # Integration endpoints (for building deep links)
    grafana_endpoint: str | None
    datadog_site: str | None
