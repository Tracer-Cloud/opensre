"""Agent-callable Slack channel reply."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework import tool
from integrations.slack.tools.slack_read_messages_tool.constants import SOURCE
from integrations.slack.tools.slack_read_messages_tool.validation import validate_channel_id
from integrations.slack.tools.slack_send_message_tool.validation import validate_message
from integrations.slack.web_client import (
    bot_token_configured,
    post_channel_message,
    resolve_bot_token,
    resolve_channel_id,
)


class SlackReplyMessageTool(BaseTool):
    """Post a message to a Slack channel or thread the bot is a member of."""

    name = "slack_reply_message"
    source = SOURCE
    description = (
        "Post a plain-text message to ANOTHER Slack channel or thread (not the one "
        "you are currently chatting in) using the bot token. Unlike slack_send_message "
        "(webhook, fixed channel), this targets any channel the bot has joined. To "
        "answer the user in the CURRENT Slack conversation, just return your reply as "
        "text — the gateway delivers it in-thread; do NOT call this tool for that. "
        "Pass a channel ID (C0123ABCD) or #channel-name."
    )
    use_cases = [
        "Replying in a specific incident channel or thread",
        "Posting a status update to a channel other than the default webhook channel",
        "Answering a question in the channel where it was asked",
    ]
    anti_examples = [
        "Publishing a long multi-section report as one message (send a short summary instead)",
        "Posting to a channel the bot has not been invited to",
    ]
    requires = ["slack"]
    side_effect_level = SideEffectLevel.EXTERNAL
    input_schema = {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": ("Slack channel ID (C…/D…/G…) or #channel-name the bot is in."),
            },
            "message": {
                "type": "string",
                "description": "Plain-text message body. Long messages are truncated.",
            },
            "thread_ts": {
                "type": "string",
                "description": "Optional parent message ts to reply in that thread.",
            },
        },
        "required": ["channel_id", "message"],
        "additionalProperties": False,
    }
    outputs = {
        "status": "'sent' on success, 'failed' otherwise",
        "sent": "boolean delivery result for easy downstream checks",
        "channel_id": "resolved Slack channel ID that was posted to",
        "error": "error detail when status is 'failed'",
        "error_type": "stable failure class: validation_error, configuration_error, or api_error",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return bot_token_configured(sources)

    def run(
        self,
        channel_id: str,
        message: str,
        thread_ts: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        channel_valid, normalized_ref, channel_error = validate_channel_id(channel_id)
        if not channel_valid:
            return self._failed(error=channel_error, error_type="validation_error")

        message_valid, normalized_message, message_error = validate_message(message)
        if not message_valid:
            return self._failed(error=message_error, error_type="validation_error")

        target, resolution_error = resolve_bot_token()
        if target is None:
            return self._failed(
                error=resolution_error,
                error_type="configuration_error",
                available=False,
            )

        resolved_id, resolve_error = resolve_channel_id(target, normalized_ref)
        if resolved_id is None:
            return self._failed(
                error=resolve_error,
                error_type="api_error" if normalized_ref.startswith("#") else "validation_error",
            )

        ok, error = post_channel_message(
            target,
            channel_id=resolved_id,
            text=normalized_message,
            thread_ts=str(thread_ts or "").strip(),
        )
        if not ok:
            return self._failed(error=error, error_type="api_error", channel_id=resolved_id)
        return {
            "source": SOURCE,
            "available": True,
            "status": "sent",
            "sent": True,
            "channel_id": resolved_id,
        }

    @staticmethod
    def _failed(
        *,
        error: str,
        error_type: str,
        available: bool = True,
        channel_id: str = "",
    ) -> dict[str, Any]:
        return {
            "source": SOURCE,
            "available": available,
            "status": "failed",
            "sent": False,
            "channel_id": channel_id,
            "error": error,
            "error_type": error_type,
        }


slack_reply_message = tool(
    SlackReplyMessageTool(),
    surfaces=(ToolSurface.ACTION,),
)
