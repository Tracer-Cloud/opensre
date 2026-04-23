"""Main orchestration node for report generation and publishing."""

import logging
import os
from typing import Optional, cast

from langchain_core.runnables import RunnableConfig
from langsmith import traceable

from app.masking import MaskingContext
from app.nodes.publish_findings.formatters.report import (
    build_slack_blocks,
    format_slack_message,
    get_investigation_url,
)
from app.nodes.publish_findings.renderers.editor import open_in_editor
from app.nodes.publish_findings.renderers.terminal import render_report
from app.nodes.publish_findings.report_context import build_report_context
from app.state import InvestigationState
from app.utils.ingest_delivery import send_ingest

logger = logging.getLogger(__name__)


def _build_mr_note(slack_message: str) -> str:
    body = slack_message.strip()
    if len(body) > 4000:
        body = body[:3997] + "..."

    return f"### RCA Finding\n\n<details>\n<summary>Investigation summary</summary>\n\n{body}\n\n</details>"


def generate_report(state: InvestigationState) -> dict:
    """Generate and publish the final RCA report."""
    from app.utils.slack_delivery import build_action_blocks, send_slack_report

    ctx = build_report_context(state)
    short_summary = state.get("problem_md")
    slack_message = format_slack_message(ctx)

    # Restore any masked infrastructure identifiers in user-facing output.
    # No-op when masking is disabled or the state has no placeholders.
    masking_ctx = MaskingContext.from_state(dict(state))
    slack_message = masking_ctx.unmask(slack_message)
    if isinstance(short_summary, str):
        short_summary = masking_ctx.unmask(short_summary)

    # First ingest: persist the report and get back the investigation_id
    investigation_id: str | None = None
    try:
        state_with_report = cast(
            InvestigationState,
            {**state, "problem_report": {"report_md": slack_message}, "summary": short_summary},
        )
        investigation_id = send_ingest(state_with_report)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[publish] ingest failed: %s", exc)

    investigation_url = get_investigation_url(state.get("organization_slug"), investigation_id)

    # Second ingest: update the record with the investigation_url so the web app can link to it
    if investigation_id:
        try:
            state_with_url = cast(
                InvestigationState,
                {
                    **state,
                    "problem_report": {
                        "report_md": slack_message,
                        "investigation_url": investigation_url,
                    },
                    "summary": short_summary,
                },
            )
            send_ingest(state_with_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[publish] ingest url update failed: %s", exc)

    all_blocks = build_slack_blocks(ctx) + build_action_blocks(investigation_url, investigation_id)
    all_blocks = masking_ctx.unmask_value(all_blocks)
    render_report(slack_message, root_cause_category=state.get("root_cause_category"))
    open_in_editor(slack_message)

    slack_ctx = state.get("slack_context", {})
    thread_ts = slack_ctx.get("thread_ts") or slack_ctx.get("ts")
    _channel = slack_ctx.get("channel_id")
    _token = slack_ctx.get("access_token")
    _alert_ts = slack_ctx.get("ts") or slack_ctx.get("thread_ts")

    resolved = state.get("resolved_integrations") or {}
    discord_creds = resolved.get("discord", {})
    logger.debug("[publish] slack_ctx=%s", slack_ctx)
    logger.debug(
        "[publish] discord creds present=%s keys=%s",
        bool(discord_creds),
        list(discord_creds.keys()) if discord_creds else [],
    )

    report_posted, delivery_error = send_slack_report(
        slack_message,
        channel=_channel,
        thread_ts=thread_ts,
        access_token=_token,
        blocks=all_blocks,
    )

    logger.debug(
        "[publish] slack delivery: posted=%s channel=%s thread_ts=%s error=%s",
        report_posted,
        _channel,
        thread_ts,
        delivery_error,
    )
    if report_posted and _token and _channel and _alert_ts:
        from app.utils.slack_delivery import swap_reaction

        swap_reaction("eyes", "clipboard", _channel, _alert_ts, _token)
    elif thread_ts and not report_posted:
        raise RuntimeError(
            f"[publish] Slack delivery failed: channel={_channel}, thread_ts={thread_ts}, reason={delivery_error}"
        )

    # Discord delivery — uses integration credentials if configured
    if discord_creds:
        from app.utils.discord_delivery import send_discord_report

        discord_ctx = state.get("discord_context") or {}
        bot_token = discord_ctx.get("bot_token") or discord_creds.get("bot_token", "")
        channel_id = discord_ctx.get("channel_id") or discord_creds.get("default_channel_id", "")
        thread_id = discord_ctx.get("thread_id", "")
        logger.debug(
            "[publish] discord delivery: channel_id=%s thread_id=%s bot_token_present=%s",
            channel_id,
            thread_id,
            bool(bot_token),
        )
        if bot_token and channel_id:
            discord_posted, discord_error = send_discord_report(
                slack_message,
                {"bot_token": bot_token, "channel_id": channel_id, "thread_id": thread_id},
            )
            logger.debug(
                "[publish] discord delivery: posted=%s error=%s", discord_posted, discord_error
            )
            if not discord_posted:
                logger.warning(
                    "[publish] Discord delivery failed: channel=%s error=%s",
                    channel_id,
                    discord_error,
                )
        else:
            logger.debug(
                "[publish] discord delivery: skipped — bot_token_present=%s channel_id=%s",
                bool(bot_token),
                channel_id,
            )
    else:
        logger.debug("[publish] discord delivery: no discord integration configured")

    # Telegram delivery — uses integration credentials if configured
    telegram_creds = resolved.get("telegram", {})
    if telegram_creds:
        from app.utils.telegram_delivery import send_telegram_report

        telegram_ctx = state.get("telegram_context") or {}
        bot_token = telegram_ctx.get("bot_token") or telegram_creds.get("bot_token", "")
        chat_id = telegram_ctx.get("chat_id") or telegram_creds.get("default_chat_id", "")
        reply_to = str(telegram_ctx.get("reply_to_message_id") or "")
        logger.debug(
            "[publish] telegram delivery: chat_id=%s reply_to=%s bot_token_present=%s",
            chat_id,
            reply_to,
            bool(bot_token),
        )
        if bot_token and chat_id:
            tg_posted, tg_error = send_telegram_report(
                slack_message,
                {"bot_token": bot_token, "chat_id": chat_id, "reply_to_message_id": reply_to},
            )
            logger.debug("[publish] telegram delivery: posted=%s error=%s", tg_posted, tg_error)
            if not tg_posted:
                logger.warning(
                    "[publish] Telegram delivery failed: chat_id=%s error=%s",
                    chat_id,
                    tg_error,
                )
        else:
            logger.debug(
                "[publish] telegram delivery: skipped — bot_token_present=%s chat_id=%s",
                bool(bot_token),
                chat_id,
            )
    else:
        logger.debug("[publish] telegram delivery: no telegram integration configured")

    # GitLab MR write-back (opt-in via GITLAB_MR_WRITEBACK env var)
    if os.getenv("GITLAB_MR_WRITEBACK", "").lower() in ("true", "1", "yes"):
        _gl = (state.get("available_sources") or {}).get("gitlab", {})
        _mr_iid = _gl.get("merge_request_iid", "")
        _project_id = _gl.get("project_id", "")
        if _mr_iid and _project_id:
            try:
                from app.integrations.gitlab import build_gitlab_config, post_gitlab_mr_note

                _gl_config = build_gitlab_config(
                    {
                        "base_url": _gl.get("gitlab_url", ""),
                        "auth_token": _gl.get("gitlab_token", ""),
                    }
                )
                post_gitlab_mr_note(
                    config=_gl_config,
                    project_id=_project_id,
                    mr_iid=_mr_iid,
                    body=_build_mr_note(slack_message),
                )
                logger.info(
                    "[publish] GitLab MR note posted: project=%s mr_iid=%s", _project_id, _mr_iid
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[publish] GitLab MR write-back failed: %s", exc)

    return {"slack_message": slack_message, "report": slack_message}


@traceable(name="node_publish_findings")
def node_publish_findings(
    state: InvestigationState,
    config: Optional[RunnableConfig] = None,  # noqa: ARG001,UP007,UP045
) -> dict:
    """LangGraph node wrapper with LangSmith tracking."""
    return generate_report(state)
