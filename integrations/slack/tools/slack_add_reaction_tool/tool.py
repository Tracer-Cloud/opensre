"""Agent-callable Slack message reaction."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework import tool
from integrations.slack.tools.slack_read_messages_tool.constants import SOURCE
from integrations.slack.tools.slack_read_messages_tool.validation import validate_channel_id
from integrations.slack.web_client import (
    add_reaction,
    bot_token_configured,
    resolve_bot_token,
    resolve_channel_id,
)


class SlackAddReactionTool(BaseTool):
    """Add an emoji reaction to a Slack message."""

    name = "slack_add_reaction"
    source = SOURCE
    description = (
        "Add an emoji reaction (reactions.add) to a Slack message. Pass channel ID "
        "or #name, the message ts, and the emoji name without colons (e.g. eyes, "
        "white_check_mark). Requires reactions:write."
    )
    use_cases = [
        "Acknowledging a message while working",
        "Marking a thread as done with a checkmark",
    ]
    anti_examples = [
        "Posting a full text reply (use slack_reply_message)",
    ]
    requires = ["slack"]
    side_effect_level = SideEffectLevel.EXTERNAL
    requires_approval = False
    input_schema = {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "Slack channel ID (C…) or #channel-name.",
            },
            "timestamp": {
                "type": "string",
                "description": "Message ts to react to.",
            },
            "emoji": {
                "type": "string",
                "description": "Emoji name without colons (e.g. eyes).",
            },
        },
        "required": ["channel_id", "timestamp", "emoji"],
        "additionalProperties": False,
    }
    outputs = {
        "status": "'reacted' on success, 'failed' otherwise",
        "channel_id": "resolved channel ID",
        "error": "error detail when status is 'failed'",
        "error_type": "validation_error, configuration_error, or api_error",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return bot_token_configured(sources)

    def run(
        self,
        channel_id: str,
        timestamp: str,
        emoji: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        valid, normalized_ref, validation_error = validate_channel_id(channel_id)
        if not valid:
            return self._failed(validation_error, "validation_error")
        ts = str(timestamp or "").strip()
        if not ts:
            return self._failed("timestamp cannot be empty.", "validation_error")

        target, resolution_error = resolve_bot_token()
        if target is None:
            return self._failed(resolution_error, "configuration_error", available=False)

        resolved_id, resolve_error = resolve_channel_id(target, normalized_ref)
        if resolved_id is None:
            return self._failed(resolve_error, "api_error")

        ok, error = add_reaction(target, channel_id=resolved_id, timestamp=ts, emoji=emoji)
        if not ok:
            return self._failed(error, "api_error", channel_id=resolved_id)
        return {
            "source": SOURCE,
            "available": True,
            "status": "reacted",
            "channel_id": resolved_id,
        }

    @staticmethod
    def _failed(
        error: str,
        error_type: str,
        *,
        available: bool = True,
        channel_id: str = "",
    ) -> dict[str, Any]:
        return {
            "source": SOURCE,
            "available": available,
            "status": "failed",
            "error": error,
            "error_type": error_type,
            "channel_id": channel_id,
        }


slack_add_reaction = tool(
    SlackAddReactionTool(),
    surfaces=(ToolSurface.CHAT, ToolSurface.ACTION),
)
