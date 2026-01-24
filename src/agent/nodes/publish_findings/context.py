"""Context building for publish findings."""

from src.agent.state import InvestigationState


def build_report_context(state: InvestigationState) -> dict:
    """Build report context from state."""
    evidence = state.get("evidence", {})
    run = evidence.get("pipeline_run", {}) or {}
    batch = evidence.get("batch_jobs", {}) or {}
    s3 = evidence.get("s3", {}) or {}

    return {
        "affected_table": state["affected_table"],
        "root_cause": state["root_cause"],
        "confidence": state["confidence"],
        "s3_marker_exists": s3.get("marker_exists", False),
        "tracer_run_status": run.get("status"),
        "tracer_run_name": run.get("run_name"),
        "tracer_pipeline_name": run.get("pipeline_name"),
        "tracer_run_cost": run.get("run_cost_usd", 0),
        "tracer_max_ram_gb": run.get("max_ram_gb", 0),
        "tracer_user_email": run.get("user_email"),
        "tracer_team": run.get("team"),
        "tracer_instance_type": run.get("instance_type"),
        "tracer_failed_tasks": 0,
        "batch_failure_reason": batch.get("failure_reason"),
        "batch_failed_jobs": batch.get("failed_jobs", 0),
    }
