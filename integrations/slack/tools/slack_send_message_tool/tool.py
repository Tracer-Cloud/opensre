"""Agent-callable Slack message action."""

from __future__ import annotations

import os
from typing import Any

from config.constants.slack import SLACK_WEBHOOK_URL_ENV
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework import tool
from integrations.slack.tools.slack_send_message_tool.constants import SOURCE
from integrations.slack.tools.slack_send_message_tool.delivery import (
    dispatch_message,
    resolve_webhook_url,
)
from integrations.slack.tools.slack_send_message_tool.results import failed_result, sent_result
from integrations.slack.tools.slack_send_message_tool.validation import validate_message
from integrations.slack.web_client import (
    bot_token_configured,
    post_channel_message,
    resolve_bot_token,
)


class SlackSendMessageTool(BaseTool):
    """Send a plain-text message via a Slack incoming webhook or bot token."""

    name = "slack_send_message"
    source = SOURCE
    description = (
        "Send a plain-text message to Slack. Uses the configured incoming webhook "
        "when one exists, otherwise posts to channel_id with the bot token. Use this "
        "for explicit user-requested Slack notifications, status updates, or on-demand "
        "alerts. Credentials are resolved internally and never returned. Put the real "
        "content in `message`: run any lookup first and send its actual result. When "
        "the user names a channel or thread, prefer slack_reply_message."
    )
    use_cases = [
        "Sending a user-requested notification to the configured Slack channel",
        "Posting a concise status update after an investigation or action",
        "Alerting a team channel with a short on-demand message",
    ]
    anti_examples = [
        "Publishing a full RCA report (use the investigation publish flow instead)",
        "Replying in an existing Slack thread without thread context",
    ]
    requires = ["slack"]
    side_effect_level = SideEffectLevel.EXTERNAL
    input_schema = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": (
                    "Plain-text message body. Long messages are truncated to Slack's limit."
                ),
            },
            "webhook_url": {
                "type": "string",
                "description": (
                    "Optional Slack incoming webhook URL. Defaults to SLACK_WEBHOOK_URL or "
                    "the configured Slack integration when omitted."
                ),
            },
            "channel_id": {
                "type": "string",
                "description": (
                    "Target channel ID (e.g. C0123ABCD). Required when sending with a bot "
                    "token; ignored when a webhook is configured, since a webhook posts to "
                    "the one channel it was created for."
                ),
            },
        },
        "required": ["message"],
        "additionalProperties": False,
    }
    outputs = {
        "status": "delivery dispatch status - 'sent' or 'failed'",
        "sent": "boolean delivery result for easy downstream checks",
        "error": "error detail when status is 'failed'",
        "error_type": "stable failure class: validation_error, configuration_error, or delivery_error",
        "message_length": "length of the normalized message submitted for delivery",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        slack = sources.get("slack") or {}
        configured_webhook = str(slack.get("webhook_url") or "").strip()
        if not configured_webhook and isinstance(slack.get("config"), dict):
            configured_webhook = str(slack["config"].get("webhook_url") or "").strip()
        env_webhook = os.getenv(SLACK_WEBHOOK_URL_ENV, "").strip()
        # A bot token posts through chat.postMessage, the same path the
        # scheduler's delivery uses. Requiring a webhook here left a token-only
        # install with no way to send while the prompt still named this tool.
        return bool(configured_webhook or env_webhook or bot_token_configured(sources))

    # extract_params intentionally stays empty. It is serialized into tool-call
    # traces, so Slack webhook URLs must be resolved inside run() only.

    def _send_with_bot_token(self, message: str, channel_id: str) -> dict[str, Any] | None:
        """Post via ``chat.postMessage``; ``None`` when no bot token is available.

        A webhook carries its own destination, so it needs no channel. A bot
        token does not — without one there is nowhere to post, and saying so
        beats reporting a send that reached nobody.
        """
        bot_target, _token_error = resolve_bot_token()
        if bot_target is None:
            return None
        if not channel_id:
            return failed_result(
                available=True,
                error="channel_id is required to send with a Slack bot token (no webhook configured).",
                error_type="configuration_error",
                message_length=len(message),
            )
        ok, error = post_channel_message(bot_target, channel_id=channel_id, text=message)
        if not ok:
            return failed_result(
                available=True,
                error=error,
                error_type="delivery_error",
                message_length=len(message),
            )
        return sent_result(message_length=len(message))

    def run(
        self,
        message: str,
        webhook_url: str = "",
        channel_id: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        valid, normalized_message, validation_error = validate_message(message)
        if not valid:
            return failed_result(
                available=True,
                error=validation_error,
                error_type="validation_error",
            )

        target, resolution_error = resolve_webhook_url(str(webhook_url or "").strip())
        if target is None:
            bot_result = self._send_with_bot_token(
                normalized_message, str(channel_id or "").strip()
            )
            if bot_result is not None:
                return bot_result
            return failed_result(
                available=False,
                error=resolution_error,
                error_type="configuration_error",
                message_length=len(normalized_message),
            )

        ok, error = dispatch_message(normalized_message, target)
        if not ok:
            return failed_result(
                available=True,
                error=error,
                error_type="delivery_error",
                message_length=len(normalized_message),
            )
        return sent_result(message_length=len(normalized_message))


slack_send_message = tool(
    SlackSendMessageTool(),
    surfaces=(ToolSurface.ACTION,),
)
