"""Slack-specific work-item delivery validation."""

from __future__ import annotations

from core.domain.work_items import WorkItemChannelTarget
from infrastructure.scheduling.scheduler.credentials import (
    resolve_slack_credentials,
    resolve_slack_default_chat_id,
)
from infrastructure.scheduling.scheduler.types import Provider


def validate_slack_target(target: WorkItemChannelTarget) -> bool | None:
    """Return Slack target validity, or ``None`` for another provider."""
    if target.provider != Provider.SLACK.value:
        return None
    if target.chat_id:
        return True
    return bool(
        resolve_slack_credentials({}).get("webhook_url") or resolve_slack_default_chat_id({})
    )


def slack_target_error(target: WorkItemChannelTarget) -> str:
    """Return the stable validation error for an undeliverable Slack target."""
    del target
    return "slack: missing chat_id; configure a Slack webhook or default channel"


_slack_target_error = slack_target_error
_validate_slack_target = validate_slack_target

__all__ = [
    "_slack_target_error",
    "_validate_slack_target",
    "slack_target_error",
    "validate_slack_target",
]
