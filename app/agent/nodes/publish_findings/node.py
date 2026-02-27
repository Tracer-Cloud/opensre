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
from app.agent.state import InvestigationState
from app.agent.utils.ingest_delivery import send_ingest

logger = logging.getLogger(__name__)


def generate_report(state: InvestigationState) -> dict:
    """Generate and publish the final RCA report."""
    from app.agent.utils.slack_delivery import build_action_blocks, send_slack_report

    ctx = build_report_context(state)
    slack_message = format_slack_message(ctx)
    render_report(slack_message)

    slack_ctx = state.get("slack_context", {})
    thread_ts = slack_ctx.get("thread_ts") or slack_ctx.get("ts")

    logger.info(
        "[publish] Slack delivery context: channel=%s, thread_ts=%s, has_access_token=%s",
        slack_ctx.get("channel_id"),
        thread_ts,
        bool(slack_ctx.get("access_token")),
    )

    investigation_url = get_investigation_url(state.get("organization_slug"))
    all_blocks = build_slack_blocks(ctx) + build_action_blocks(investigation_url)

    logger.info("[publish] Sending report: text_len=%d, blocks=%d", len(slack_message), len(all_blocks))

    send_slack_report(
        slack_message,
        channel=slack_ctx.get("channel_id"),
        thread_ts=thread_ts,
        access_token=slack_ctx.get("access_token"),
        blocks=all_blocks,
    )

    _channel = slack_ctx.get("channel_id")
    _token = slack_ctx.get("access_token")
    _alert_ts = slack_ctx.get("ts") or slack_ctx.get("thread_ts")
    if _token and _channel and _alert_ts:
        from app.agent.utils.slack_delivery import swap_reaction
        swap_reaction("eyes", "clipboard", _channel, _alert_ts, _token)

    try:
        send_ingest(state)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[publish] Ingest delivery failed: %s", exc)

    return {"slack_message": slack_message}


@traceable(name="node_publish_findings")
def node_publish_findings(state: InvestigationState) -> dict:
    """LangGraph node wrapper with LangSmith tracking."""
    return generate_report(state)
