"""Stable result shapes for Slack history reads."""

from __future__ import annotations

from typing import Any

from integrations.slack.tools.slack_read_messages_tool.constants import SOURCE


def failed_result(*, available: bool, error: str, error_type: str) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "available": available,
        "status": "failed",
        "error": error,
        "error_type": error_type,
        "messages": [],
        "message_count": 0,
        "truncated": False,
    }


def read_result(
    *, channel_id: str, messages: list[dict[str, str]], truncated: bool
) -> dict[str, Any]:
    # A success result must NOT carry an "error" key: the shared tool runtime
    # flags any dict containing "error" as a failed tool call regardless of value.
    return {
        "source": SOURCE,
        "available": True,
        "status": "read",
        "channel_id": channel_id,
        "messages": messages,
        "message_count": len(messages),
        "truncated": truncated,
    }
