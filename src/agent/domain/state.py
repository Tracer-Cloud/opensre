"""
Investigation state definition.

The single source of truth for state shape across the graph.
"""

from typing import TypedDict, Any


class InvestigationState(TypedDict, total=False):
    """State passed through the investigation graph."""
    
    # Input - from alert
    alert_name: str
    affected_table: str
    severity: str
    
    # Hypotheses - proposed before investigation
    hypotheses: list[dict[str, Any]]
    
    # Evidence - from tool calls
    s3_marker_exists: bool
    s3_file_count: int
    
    # Tracer run data (from /api/batch-runs)
    tracer_run_found: bool
    tracer_run_id: str | None
    tracer_pipeline_name: str | None
    tracer_run_name: str | None
    tracer_run_status: str | None
    tracer_run_time_seconds: float
    tracer_run_cost: float
    tracer_max_ram_gb: float
    tracer_user_email: str | None
    tracer_team: str | None
    tracer_instance_type: str | None
    
    # Tracer task data
    tracer_total_tasks: int
    tracer_failed_tasks: int
    tracer_failed_task_details: list[dict[str, Any]]
    
    # AWS Batch job data
    batch_jobs_found: bool
    batch_total_jobs: int
    batch_failed_jobs: int
    batch_failure_reason: str | None
    batch_job_details: list[dict[str, Any]]
    
    # Analysis - from LLM
    root_cause: str
    confidence: float
    
    # Outputs - formatted reports
    slack_message: str
    problem_md: str

