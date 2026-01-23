# src/agent/state.py
from typing import TypedDict, List, Optional


class InvestigationState(TypedDict):
    alert_name: str
    affected_table: str
    severity: str

    hypotheses: List[str]

    s3_marker_exists: bool
    s3_file_count: int

    tracer_run_found: bool
    tracer_run_id: Optional[str]
    tracer_pipeline_name: Optional[str]
    tracer_run_status: Optional[str]
    tracer_run_time_seconds: int
    tracer_total_tasks: int
    tracer_failed_tasks: int
    tracer_failed_task_details: List[dict]

    root_cause: str
    confidence: float
    slack_message: str
    problem_md: str


def make_initial_state(alert_name: str, affected_table: str, severity: str) -> InvestigationState:
    return {
        "alert_name": alert_name,
        "affected_table": affected_table,
        "severity": severity,
        "hypotheses": [],
        "s3_marker_exists": False,
        "s3_file_count": 0,
        "tracer_run_found": False,
        "tracer_run_id": None,
        "tracer_pipeline_name": None,
        "tracer_run_status": None,
        "tracer_run_time_seconds": 0,
        "tracer_total_tasks": 0,
        "tracer_failed_tasks": 0,
        "tracer_failed_task_details": [],
        "root_cause": "",
        "confidence": 0.0,
        "slack_message": "",
        "problem_md": "",
    }
