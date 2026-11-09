"""Credential resolution and transport dispatch for Slack messages."""

from __future__ import annotations

import os

from config.constants.slack import SLACK_WEBHOOK_URL_ENV
from integrations.slack.delivery import send_slack_webhook_message
from integrations.slack.tools.slack_send_message_tool.models import SlackDeliveryTarget


def resolve_webhook_url(webhook_url: str = "") -> tuple[SlackDeliveryTarget | None, str]:
    """Resolve a Slack incoming webhook URL from the caller or integration store."""
    explicit = str(webhook_url or "").strip()
    if explicit:
        return SlackDeliveryTarget(webhook_url=explicit), ""

    env_url = os.getenv(SLACK_WEBHOOK_URL_ENV, "").strip()
    if env_url:
        return SlackDeliveryTarget(webhook_url=env_url), ""

    try:
        from integrations.catalog import resolve_effective_integrations

        slack_integration = resolve_effective_integrations().get("slack") or {}
        config = slack_integration.get("config") if isinstance(slack_integration, dict) else {}
        stored_url = str(config.get("webhook_url", "") if isinstance(config, dict) else "").strip()
    except Exception as exc:
        return None, str(exc)

    if not stored_url:
        return None, (
            "Slack is not configured. Set SLACK_WEBHOOK_URL or configure the Slack integration."
        )
    return SlackDeliveryTarget(webhook_url=stored_url), ""


def dispatch_message(message: str, target: SlackDeliveryTarget) -> tuple[bool, str]:
    """Post a message via the resolved Slack incoming webhook."""
    ok, error = send_slack_webhook_message(message, webhook_url=target.webhook_url)
    if ok:
        return True, ""
    if error == "no_webhook":
        return False, "Slack webhook URL is not configured."
    return False, "Slack webhook delivery failed."
