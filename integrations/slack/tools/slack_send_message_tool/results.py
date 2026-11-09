"""Stable result shapes for Slack message delivery."""

from __future__ import annotations

from typing import Any

from integrations.slack.tools.slack_send_message_tool.constants import SOURCE


def failed_result(
    *,
    available: bool,
    error: str,
    error_type: str,
    message_length: int = 0,
) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "available": available,
        "status": "failed",
        "sent": False,
        "error": error,
        "error_type": error_type,
        "message_length": message_length,
    }


def sent_result(*, message_length: int) -> dict[str, Any]:
    # A success result must NOT carry an "error" key: the shared tool runtime
    # (`core.tool.execution._normalize_result`) flags any dict containing "error" as a
    # failed tool call regardless of its value, which would make a delivered
    # message look failed to the model and the turn accounting.
    return {
        "source": SOURCE,
        "available": True,
        "status": "sent",
        "sent": True,
        "message_length": message_length,
    }
