"""Agent-callable Slack channel join."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.slack.tools.slack_read_messages_tool.constants import SOURCE
from integrations.slack.tools.slack_read_messages_tool.validation import validate_channel_id
from integrations.slack.web_client import (
    bot_token_configured,
    join_channel,
    resolve_bot_token,
    resolve_channel_id,
)


class SlackJoinChannelTool(BaseTool):
    """Join a public Slack channel so the bot can read and post there."""

    name = "slack_join_channel"
    source = SOURCE
    description = (
        "Join a public Slack channel (conversations.join) using the bot token so "
        "later read/reply tools work. Pass a channel ID (C…) or #channel-name. "
        "Private channels still require a human /invite."
    )
    use_cases = [
        "Joining #incidents before reading recent messages",
        "Ensuring the bot is in a channel before posting a status update",
    ]
    anti_examples = [
        "Joining a private channel without an invite (will fail)",
        "Using this instead of reading messages",
    ]
    requires = ["slack"]
    side_effect_level = SideEffectLevel.EXTERNAL
    input_schema = {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "Slack channel ID (C…) or #channel-name.",
            },
        },
        "required": ["channel_id"],
        "additionalProperties": False,
    }
    outputs = {
        "status": "'joined' on success, 'failed' otherwise",
        "channel_id": "resolved channel ID",
        "error": "error detail when status is 'failed'",
        "error_type": "validation_error, configuration_error, or api_error",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return bot_token_configured(sources)

    def run(self, channel_id: str, **_kwargs: Any) -> dict[str, Any]:
        valid, normalized_ref, validation_error = validate_channel_id(channel_id)
        if not valid:
            return {
                "source": SOURCE,
                "available": True,
                "status": "failed",
                "error": validation_error,
                "error_type": "validation_error",
                "channel_id": "",
            }

        target, resolution_error = resolve_bot_token()
        if target is None:
            return tool_unavailable(
                SOURCE,
                resolution_error,
                status="failed",
                error_type="configuration_error",
                channel_id="",
            )

        resolved_id, resolve_error = resolve_channel_id(target, normalized_ref)
        if resolved_id is None:
            return {
                "source": SOURCE,
                "available": True,
                "status": "failed",
                "error": resolve_error,
                "error_type": "api_error",
                "channel_id": "",
            }

        ok, error = join_channel(target, channel_id=resolved_id)
        if not ok:
            return {
                "source": SOURCE,
                "available": True,
                "status": "failed",
                "error": error,
                "error_type": "api_error",
                "channel_id": resolved_id,
            }
        return {
            "source": SOURCE,
            "available": True,
            "status": "joined",
            "channel_id": resolved_id,
        }


slack_join_channel = tool(
    SlackJoinChannelTool(),
    surfaces=(ToolSurface.CHAT, ToolSurface.ACTION),
)
