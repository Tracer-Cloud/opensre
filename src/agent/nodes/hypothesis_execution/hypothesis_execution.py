"""Hypothesis execution - gather evidence to prove/disprove hypotheses using dynamic tools."""

from src.agent.nodes.hypothesis_execution.context_building import build_investigation_context
from src.agent.nodes.rca_report_publishing.render import (
    render_evidence,
    render_step_header,
)
from src.agent.state import InvestigationState
from src.agent.tools.tools import (
    get_airflow_metrics,
    get_batch_statistics,
    get_error_logs,
    get_failed_jobs,
    get_failed_tools,
    get_host_metrics,
)
from src.agent.utils import get_executed_sources

# ============================================================================
# Compatibility Functions (for backward compatibility)
# ============================================================================


def gather_evidence_for_context(context: dict) -> dict:
    """
    Compatibility wrapper for gathering evidence.

    This function gathers all evidence upfront. For new implementations,
    use tools directly with a LangGraph agent that can dynamically
    select which tools to call based on investigation needs.

    Args:
        context: Investigation context containing trace_id

    Returns:
        Dictionary with gathered evidence
    """
    # Try to get trace_id from tracer_web_run context
    tracer_web_run = context.get("tracer_web_run", {})
    trace_id = tracer_web_run.get("trace_id")

    if not trace_id:
        # Fallback to pipeline_run context
        pipeline_run = context.get("pipeline_run", {})
        trace_id = pipeline_run.get("run_id")

    if not trace_id:
        return {}

    # Call all tools to gather evidence (maintains backward compatibility)
    # In the future, this should be replaced with an agent that selects tools dynamically
    evidence = {}

    try:
        # LangChain tools are callable - call directly with keyword arguments
        batch_stats = get_batch_statistics(trace_id)
        if isinstance(batch_stats, dict) and "error" not in batch_stats:
            evidence["batch_stats"] = batch_stats
    except Exception:
        pass

    try:
        failed_tools_data = get_failed_tools(trace_id)
        if isinstance(failed_tools_data, dict) and "error" not in failed_tools_data:
            evidence["failed_tools"] = failed_tools_data.get("failed_tools", [])
            evidence["failed_tools_source"] = failed_tools_data.get("source")
            evidence["total_tools"] = failed_tools_data.get("total_tools", 0)
    except Exception:
        pass

    try:
        failed_jobs_data = get_failed_jobs(trace_id)
        if isinstance(failed_jobs_data, dict) and "error" not in failed_jobs_data:
            evidence["failed_jobs"] = failed_jobs_data.get("failed_jobs", [])
            evidence["failed_jobs_source"] = failed_jobs_data.get("source")
            evidence["total_jobs"] = failed_jobs_data.get("total_jobs", 0)
    except Exception:
        pass

    try:
        error_logs_data = get_error_logs(trace_id, size=500, error_only=True)
        if isinstance(error_logs_data, dict) and "error" not in error_logs_data:
            evidence["error_logs"] = error_logs_data.get("logs", [])
            evidence["error_logs_source"] = error_logs_data.get("source")
            evidence["total_logs"] = error_logs_data.get("total_logs", 0)
            evidence["logs_available"] = error_logs_data.get("total_logs", 0) > 0
    except Exception:
        pass

    try:
        all_logs_data = get_error_logs(trace_id, size=200, error_only=False)
        if isinstance(all_logs_data, dict) and "error" not in all_logs_data:
            evidence["all_logs"] = all_logs_data.get("logs", [])
    except Exception:
        pass

    try:
        host_metrics_data = get_host_metrics(trace_id)
        if isinstance(host_metrics_data, dict) and "error" not in host_metrics_data:
            # Store the validated metrics (which includes data_quality_issues if present)
            validated_metrics = host_metrics_data.get("metrics", {})
            evidence["host_metrics"] = validated_metrics
            evidence["host_metrics_source"] = host_metrics_data.get("source")
            # Also store validation flag
            if host_metrics_data.get("validation_performed"):
                evidence["host_metrics_validated"] = True
    except Exception:
        pass

    try:
        airflow_metrics_data = get_airflow_metrics(trace_id)
        if isinstance(airflow_metrics_data, dict) and "error" not in airflow_metrics_data:
            evidence["airflow_metrics"] = airflow_metrics_data.get("metrics")
            evidence["airflow_metrics_source"] = airflow_metrics_data.get("source")
    except Exception:
        pass

    # CloudWatch metrics require job_queue, which we'd need to extract from batch_details
    # For now, skip this in compatibility mode

    return evidence


def gather_evidence_for_trace(trace_id: str, context: dict) -> dict:  # noqa: ARG001
    """
    Gather evidence for a specific trace (compatibility function for tests).

    Args:
        trace_id: The trace/run identifier
        context: Unused, kept for compatibility

    Returns:
        Dictionary with gathered evidence
    """
    if not trace_id:
        return {}

    # Build a minimal context with trace_id
    minimal_context = {
        "tracer_web_run": {"trace_id": trace_id},
    }

    return gather_evidence_for_context(minimal_context)


# ============================================================================
# Main Node Function
# ============================================================================


def main(state: InvestigationState) -> dict:
    """
    Main entry point for hypothesis execution.

    Flow:
    1) Get context (already built in frame_problem or from state)
    2) Check what evidence has already been gathered
    3) Only gather NEW evidence for sources in plan_sources that haven't been executed
    4) Merge and return evidence
    """
    # Get context from state or build it
    context = state.get("context", {})
    if not context:
        context = build_investigation_context(state)

    # Get existing evidence and executed hypotheses
    existing_evidence = state.get("evidence", {})

    # Collect all sources that have already been executed
    executed_sources_set = get_executed_sources(state)

    # Get current plan_sources
    plan_sources = state.get("plan_sources", [])

    # Check if we should gather new evidence
    # Only gather if we have new sources in plan_sources that haven't been executed
    new_sources = [s for s in plan_sources if s not in executed_sources_set]

    # Only skip gathering if:
    # 1. No new sources to gather AND
    # 2. We have executed sources (meaning we've tried before) AND
    # 3. We have existing evidence (meaning we successfully gathered something before)
    has_existing_evidence = bool(
        existing_evidence
        and (
            existing_evidence.get("tracer_web_run", {}).get("found")
            or existing_evidence.get("pipeline_run", {}).get("found")
            or existing_evidence.get("evidence_sources_checked")
        )
    )

    if not new_sources and executed_sources_set and has_existing_evidence:
        # All sources have been executed and we have evidence - don't re-gather
        from src.agent.nodes.rca_report_publishing.render import console

        console.print(
            "  [yellow]⚠️  All planned sources have already been executed. Using existing evidence.[/]"
        )
        render_evidence(existing_evidence)
        return {"evidence": existing_evidence}

    # If we have no evidence yet, we must gather it (even if plan_sources is empty)
    if not has_existing_evidence and not new_sources:
        # No plan sources and no existing evidence - try to gather from available metadata anyway
        from src.agent.nodes.rca_report_publishing.render import console

        console.print(
            "  [yellow]⚠️  No plan sources available, but attempting to gather evidence from available metadata.[/]"
        )

    # Gather evidence only for new sources (or all if first time)
    render_step_header(1, "Gather runtime evidence")
    if new_sources:
        from src.agent.nodes.rca_report_publishing.render import console

        console.print(f"  [dim]Gathering evidence for new sources: {', '.join(new_sources)}[/]")

    runtime_evidence = gather_evidence_for_context(context)

    # Merge evidence - preserve existing evidence and add/update with new evidence
    evidence = existing_evidence.copy() if existing_evidence else {}
    evidence.update(context)  # Update with context

    tracer_web_run = evidence.get("tracer_web_run", {})
    if tracer_web_run.get("found") and runtime_evidence:
        # Merge runtime evidence into existing tracer_web_run data
        evidence["tracer_web_run"] = {**tracer_web_run, **runtime_evidence}

    pipeline_run = evidence.get("pipeline_run", {})
    if pipeline_run.get("found") and runtime_evidence:
        evidence["pipeline_run"] = {**pipeline_run, **runtime_evidence}

    evidence.setdefault("s3", {"found": False, "error": "S3 storage check is not implemented"})
    evidence.setdefault("batch_jobs", {"found": False})

    render_evidence(evidence)
    return {"evidence": evidence}


def node_hypothesis_execution(state: InvestigationState) -> dict:
    """LangGraph node wrapper."""
    return main(state)


# Backward compatibility alias
node_hypothesis_investigation = node_hypothesis_execution
