"""Context building for publish findings."""

from src.agent.state import InvestigationState


def build_report_context(state: InvestigationState) -> dict:
    """Build report context from state.

    Reads from tracer_web_run which contains the actual pipeline data.
    """
    evidence = state.get("evidence", {})

    # Primary source: tracer_web_run (from frame_problem context building)
    web_run = evidence.get("tracer_web_run", {}) or {}

    # Fallback sources for backward compatibility
    batch = evidence.get("batch_jobs", {}) or {}
    s3 = evidence.get("s3", {}) or {}

    validated_claims = state.get("validated_claims", [])
    non_validated_claims = state.get("non_validated_claims", [])
    validity_score = state.get("validity_score", 0.0)

    # Filter out junk claims (like "NON_" prefix artifacts)
    validated_claims = [
        c for c in validated_claims
        if c.get("claim", "").strip() and not c.get("claim", "").strip().startswith("NON_")
    ]

    return {
        "affected_table": state.get("affected_table", "unknown"),
        "root_cause": state.get("root_cause", ""),
        "confidence": state.get("confidence", 0.0),
        "validated_claims": validated_claims,
        "non_validated_claims": non_validated_claims,
        "validity_score": validity_score,
        "s3_marker_exists": s3.get("marker_exists", False),
        # Read from tracer_web_run (correct source)
        "tracer_run_status": web_run.get("status"),
        "tracer_run_name": web_run.get("run_name"),
        "tracer_pipeline_name": web_run.get("pipeline_name"),
        "tracer_run_cost": web_run.get("run_cost", 0),
        "tracer_max_ram_gb": web_run.get("max_ram_gb", 0),
        "tracer_user_email": web_run.get("user_email"),
        "tracer_team": web_run.get("team"),
        "tracer_instance_type": web_run.get("instance_type"),
        "tracer_failed_tasks": len(web_run.get("failed_jobs", [])),
        "batch_failure_reason": batch.get("failure_reason"),
        "batch_failed_jobs": batch.get("failed_jobs", 0),
    }
