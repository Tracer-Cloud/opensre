"""Main orchestration node for report generation and publishing."""

import logging

from langsmith import traceable

from app.agent.nodes.publish_findings.context import build_report_context
from app.agent.nodes.publish_findings.formatters.report import (
    build_slack_blocks,
    format_slack_message,
    get_investigation_url,
)
from app.agent.nodes.publish_findings.renderers.terminal import render_report
from app.agent.utils.ingest_delivery import send_ingest
from app.agent.state import InvestigationState

logger = logging.getLogger(__name__)


def generate_report(state: InvestigationState) -> dict:
    """Generate and render the final RCA report.

    This is the main entry point for report generation. It:
    1. Builds report context from investigation state
    2. Formats the Slack message
    3. Renders the report to terminal
    4. Sends to Slack (with thread reply if slack_context is present)
    5. Returns the slack_message for external use

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
    render_report(slack_message)

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
    investigation_url = get_investigation_url(state.get("organization_slug"))
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

    try:
        send_ingest(state)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[publish] Ingest delivery failed: %s", exc)

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
