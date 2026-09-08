"""Scheduled-delivery adapter: post a scheduled task's message to Slack."""

from __future__ import annotations

from http import HTTPStatus

from infrastructure.delivery.notifications.delivery_transport import post_json
from infrastructure.delivery.notifications.redaction import redact_slack_token
from infrastructure.scheduling.scheduler.credentials import resolve_slack_credentials
from infrastructure.scheduling.scheduler.delivery import (
    resolve_slack_delivery_chat_id,
    slack_can_deliver,
    strip_html,
)
from infrastructure.scheduling.scheduler.types import ScheduledTask
from integrations.slack.delivery import send_slack_webhook_message


class SlackScheduledDelivery:
    """Deliver a scheduled task's message via Slack.

    A resolved ``chat_id`` uses the bot-token ``chat.postMessage`` API (new
    top-level message); an empty ``chat_id`` uses the channel-bound incoming
    webhook. Slack uses mrkdwn, not HTML, so tags are stripped.
    """

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        creds = resolve_slack_credentials(task.params)
        access_token = str(creds.get("access_token") or "").strip()
        webhook_url = str(creds.get("webhook_url") or "").strip()
        chat_id = resolve_slack_delivery_chat_id(task, webhook_url=webhook_url)

        # Same policy as readiness: chat_id → bot token; empty chat_id → webhook OK.
        if not slack_can_deliver(creds, chat_id=chat_id):
            if chat_id and webhook_url and not access_token:
                return (
                    False,
                    "Slack bot token required when chat_id is set (a webhook alone "
                    "cannot target an explicit chat_id)",
                    "",
                )
            if not chat_id:
                return False, "Missing chat_id or webhook_url for Slack delivery", ""
            return False, "Scheduled tasks require Slack bot access_token for chat_id delivery", ""

        plain_message = strip_html(message)

        if access_token and chat_id:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=utf-8",
            }
            payload = {"channel": chat_id, "text": plain_message}
            response = post_json(
                url="https://slack.com/api/chat.postMessage",
                payload=payload,
                headers=headers,
            )
            if not response.ok:
                safe_error = redact_slack_token(response.error, access_token)
                return False, f"Slack API error: {safe_error}", ""
            if not HTTPStatus.OK <= response.status_code < HTTPStatus.MULTIPLE_CHOICES:
                error_text = (
                    redact_slack_token(response.text, access_token)[:200]
                    if response.text
                    else f"HTTP {response.status_code}"
                )
                safe_error = redact_slack_token(error_text, access_token)
                return False, f"Slack HTTP error: {safe_error}", ""
            if response.data.get("ok") is not True:
                error = response.data.get("error", "unknown")
                safe_error = redact_slack_token(str(error), access_token)
                return False, f"Slack error: {safe_error}", ""
            return True, "", str(response.data.get("ts", ""))

        ok, error = send_slack_webhook_message(plain_message, webhook_url=webhook_url)
        return ok, error, ""
