"""Compose Slack read and delivery adapters around proactive judgement."""

from __future__ import annotations

import logging
from typing import Any

from gateway.transports.slack.client import SlackMessagingClient
from infrastructure.proactive_messages import (
    ProactiveJudgementRunner,
    ProactiveMessageService,
)
from integrations.slack import (
    fetch_channel_messages,
    markdown_to_slack_mrkdwn,
    resolve_bot_token,
)


def build_proactive_message_service(
    *,
    messaging: SlackMessagingClient,
    logger: logging.Logger,
) -> ProactiveMessageService:
    """Build the event-driven proactive service for one Slack transport."""

    def _read_context(
        *,
        channel_id: str,
        thread_ts: str,
        limit: int,
    ) -> dict[str, Any]:
        target, _error = resolve_bot_token()
        if target is None:
            return {"status": "failed", "error_type": "configuration_error", "messages": []}
        messages, _error = fetch_channel_messages(
            target,
            channel_id=channel_id,
            thread_ts=thread_ts,
            limit=limit,
        )
        if messages is None:
            return {"status": "failed", "error_type": "api_error", "messages": []}
        return {
            "status": "read",
            "channel_id": channel_id,
            "messages": messages,
            "message_count": len(messages),
            "truncated": len(messages) >= limit,
        }

    def _deliver(*, channel_id: str, thread_ts: str, message: str) -> str | None:
        return messaging.post_message(
            channel=channel_id,
            thread_ts=thread_ts,
            text=markdown_to_slack_mrkdwn(message),
        )

    runner = ProactiveJudgementRunner(context_reader=_read_context, delivery=_deliver)
    logger.info("[slack-gateway] proactive judgement enabled (event-driven)")
    return ProactiveMessageService(runner)
