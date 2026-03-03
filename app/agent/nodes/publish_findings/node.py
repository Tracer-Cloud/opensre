"""Main orchestration node for report generation and publishing."""

import logging
from typing import cast

from langsmith import traceable

from app.agent.nodes.publish_findings.formatters.report import (
    build_slack_blocks,
    format_slack_message,
    get_investigation_url,
)
from app.agent.nodes.publish_findings.renderers.terminal import render_report
from app.agent.nodes.publish_findings.report_context import build_report_context
from app.agent.state import InvestigationState
from app.agent.utils.ingest_delivery import send_ingest

logger = logging.getLogger(__name__)


def generate_report(state: InvestigationState) -> dict:
    """Generate and publish the final RCA report."""
    from app.agent.utils.slack_delivery import build_action_blocks, send_slack_report

    # 1. Extract — pull structured data out of raw investigation state
    ctx = build_report_context(state)
    short_summary = state.get("problem_md")  # preserve short summary for list views

    # 2. Format — render ctx into Slack mrkdwn text + Block Kit blocks
    slack_message = format_slack_message(ctx)

    # 3. Push to ingest API first to get the investigation_id for the URL
    investigation_id: str | None = None
    try:
        state_with_report = cast(InvestigationState, {**state, "problem_report": {"report_md": slack_message}, "summary": short_summary})
        investigation_id = send_ingest(state_with_report)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[publish] Ingest delivery failed: %s", exc)

    investigation_url = get_investigation_url(state.get("organization_slug"), investigation_id)
    all_blocks = build_slack_blocks(ctx) + build_action_blocks(investigation_url)

    # 4. Deliver — send to terminal and Slack
    render_report(slack_message)

    slack_ctx = state.get("slack_context", {})
    thread_ts = slack_ctx.get("thread_ts") or slack_ctx.get("ts")

    logger.info(
        "[publish] Slack delivery context: channel=%s, thread_ts=%s, has_access_token=%s",
        slack_ctx.get("channel_id"),
        thread_ts,
        bool(slack_ctx.get("access_token")),
    )
    logger.info("[publish] Sending report: text_len=%d, blocks=%d", len(slack_message), len(all_blocks))

    _channel = slack_ctx.get("channel_id")
    _token = slack_ctx.get("access_token")
    _alert_ts = slack_ctx.get("ts") or slack_ctx.get("thread_ts")
    slack_delivery_attempted = bool(thread_ts)

    report_posted, delivery_error = send_slack_report(
        slack_message,
        channel=_channel,
        thread_ts=thread_ts,
        access_token=_token,
        blocks=all_blocks,
    )

    if report_posted and _token and _channel and _alert_ts:
        from app.agent.utils.slack_delivery import swap_reaction
        swap_reaction("eyes", "clipboard", _channel, _alert_ts, _token)
    elif slack_delivery_attempted and not report_posted:
        raise RuntimeError(
            f"[publish] Slack delivery failed: channel={_channel}, thread_ts={thread_ts}, reason={delivery_error}"
        )

    return {"slack_message": slack_message}


@traceable(name="node_publish_findings")
def node_publish_findings(state: InvestigationState) -> dict:
    """LangGraph node wrapper with LangSmith tracking."""
    return generate_report(state)
