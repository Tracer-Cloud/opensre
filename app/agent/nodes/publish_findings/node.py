"""Main orchestration node for report generation and publishing."""

import logging

from langsmith import traceable

from app.agent.nodes.publish_findings.context import build_report_context
from app.agent.nodes.publish_findings.formatters.lineage import format_data_lineage_flow
from app.agent.nodes.publish_findings.formatters.report import (
    build_slack_blocks,
    format_slack_message,
    get_investigation_url,
)
from app.agent.nodes.publish_findings.renderers.terminal import render_report
from app.agent.state import InvestigationState

logger = logging.getLogger(__name__)


def _persist_memory(state: InvestigationState, slack_message: str) -> None:
    """Persist investigation results to memory if enabled.

    Args:
        state: Investigation state
        slack_message: Formatted RCA report
    """
    from app.agent.memory import is_memory_enabled, write_memory

    if not is_memory_enabled():
        return

    # Extract context for memory
    context = state.get("context", {})
    evidence = state.get("evidence", {})
    web_run = context.get("tracer_web_run", {}) or {}

    pipeline_name = web_run.get("pipeline_name") or state.get("pipeline_name", "unknown")
    raw_alert_value = state.get("raw_alert", {})
    raw_alert_dict = raw_alert_value if isinstance(raw_alert_value, dict) else {}
    alert_id = raw_alert_dict.get("alert_id", "unknown")
    root_cause = state.get("root_cause", "")
    confidence = state.get("confidence", 0.0)
    validity_score = state.get("validity_score", 0.0)

    # Extract action sequence from executed hypotheses
    executed_hypotheses = state.get("executed_hypotheses", [])
    action_sequence = []
    if executed_hypotheses:
        for hyp in executed_hypotheses:
            actions = hyp.get("actions", [])
            if isinstance(actions, list):
                action_sequence.extend(actions)

    # Extract data lineage (use same context building as report)
    from app.agent.nodes.publish_findings.context.models import ReportContext

    ctx: ReportContext = {
        "pipeline_name": pipeline_name,
        "root_cause": root_cause,
        "confidence": confidence,
        "validated_claims": state.get("validated_claims", []),
        "non_validated_claims": state.get("non_validated_claims", []),
        "validity_score": validity_score,
        "s3_marker_exists": evidence.get("s3", {}).get("marker_exists", False),
        "tracer_run_status": web_run.get("status"),
        "tracer_run_name": web_run.get("run_name"),
        "tracer_pipeline_name": web_run.get("pipeline_name"),
        "tracer_run_cost": web_run.get("run_cost", 0),
        "tracer_max_ram_gb": web_run.get("max_ram_gb", 0),
        "tracer_user_email": web_run.get("user_email"),
        "tracer_team": web_run.get("team"),
        "tracer_instance_type": web_run.get("instance_type"),
        "tracer_failed_tasks": len(evidence.get("failed_jobs", [])),
        "batch_failure_reason": evidence.get("batch_jobs", {}).get("failure_reason"),
        "batch_failed_jobs": evidence.get("batch_jobs", {}).get("failed_jobs", 0),
        "cloudwatch_log_group": None,
        "cloudwatch_log_stream": None,
        "cloudwatch_logs_url": None,
        "cloudwatch_region": None,
        "alert_id": alert_id,
        "evidence": evidence,
        "raw_alert": raw_alert_dict,
    }

    lineage_section = format_data_lineage_flow(ctx)

    # Extract problem pattern (first sentence of root cause)
    problem_pattern = root_cause.split(".")[0] if root_cause else ""

    write_memory(
        pipeline_name=pipeline_name,
        alert_id=alert_id,
        root_cause=root_cause,
        confidence=confidence,
        validity_score=validity_score,
        action_sequence=action_sequence[:5],  # Top 5 actions
        data_lineage=lineage_section,
        problem_pattern=problem_pattern,
        rca_report=slack_message,  # Store full RCA report
    )


def generate_report(state: InvestigationState) -> dict:
    """Generate and render the final RCA report.

    This is the main entry point for report generation. It:
    1. Builds report context from investigation state
    2. Formats the Slack message
    3. Renders the report to terminal
    4. Persists to memory (if enabled)
    5. Sends to Slack (with thread reply if slack_context is present)
    6. Returns the slack_message for external use

    Args:
        state: Investigation state with all analysis results

    Returns:
        Dictionary with slack_message key for downstream consumers
    """
    from app.agent.utils.slack_delivery import send_slack_report

    # Build context from state
    ctx = build_report_context(state)

    # Format the report
    slack_message = format_slack_message(ctx)

    # Render to terminal
    render_report(slack_message, ctx.get("confidence", 0.0))

    # Persist to memory if enabled
    _persist_memory(state, slack_message)

    # Send to Slack - always reply in the thread of the original alert message.
    # Use thread_ts if the alert was already in a thread, otherwise use ts
    # (the alert message's own timestamp) to start a thread under it.
    from app.agent.utils.slack_delivery import build_action_blocks

    slack_ctx = state.get("slack_context", {})
    thread_ts = slack_ctx.get("thread_ts") or slack_ctx.get("ts")

    logger.info(
        "[publish] Slack delivery context: channel=%s, thread_ts=%s, has_access_token=%s",
        slack_ctx.get("channel_id"),
        thread_ts,
        bool(slack_ctx.get("access_token")),
    )

    logger.info(
        "slack_ctx: %s", slack_ctx,
    )
    investigation_url = get_investigation_url()
    report_blocks = build_slack_blocks(ctx)
    action_blocks = build_action_blocks(investigation_url)
    all_blocks = report_blocks + action_blocks

    logger.info(
        "[publish] Sending report: text_len=%d, blocks=%d",
        len(slack_message),
        len(all_blocks),
    )

    send_slack_report(
        slack_message,
        channel=slack_ctx.get("channel_id"),
        thread_ts=thread_ts,
        access_token=slack_ctx.get("access_token"),
        blocks=all_blocks,
    )

    return {"slack_message": slack_message}



@traceable(name="node_publish_findings")
def node_publish_findings(state: InvestigationState) -> dict:
    """LangGraph node wrapper with LangSmith tracking.

    Args:
        state: Investigation state

    Returns:
        Dictionary with slack_message for state update
    """
    return generate_report(state)
