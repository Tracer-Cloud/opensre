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
    root_cause: str
    confidence: float
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

    # Raw data for deeper inspection
    evidence: dict  # Raw evidence data for citation
    raw_alert: dict  # Raw alert for infrastructure extraction
