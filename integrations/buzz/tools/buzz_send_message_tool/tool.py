"""Agent-callable Buzz message action."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework import tool
from integrations.buzz.tools.buzz_send_message_tool.constants import SOURCE
from integrations.buzz.tools.buzz_send_message_tool.delivery import (
    dispatch_message,
    resolve_target,
)
from integrations.buzz.tools.buzz_send_message_tool.results import failed_result, sent_result
from integrations.buzz.tools.buzz_send_message_tool.validation import (
    normalize_optional_text,
    validate_message,
)


class BuzzSendMessageTool(BaseTool):
    """Send a plain-text message via the configured Buzz integration."""

    name = "buzz_send_message"
    source = SOURCE
    description = (
        "Send a plain-text message via the configured Buzz integration. Use it only "
        "when the user asks to send something to Buzz; do not send alerts or turn "
        "results there on your own. The tool resolves credentials internally and "
        "returns structured delivery status without exposing secrets."
    )
    use_cases = [
        "Sending a user-requested message to the configured Buzz default channel",
        "Posting an incident notification the user asked for to a Buzz channel",
        "Sending a short status update the user asked for after an investigation",
    ]
    requires = ["buzz"]
    side_effect_level = SideEffectLevel.EXTERNAL
    input_schema = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": (
                    "Plain-text message body. Long messages are truncated to the "
                    "shared 4096-character messaging limit."
                ),
            },
            "channel": {
                "type": "string",
                "description": (
                    "Optional Buzz channel UUID (see `buzz channels list`). "
                    "Defaults to the configured default_channel."
                ),
            },
        },
        "required": ["message"],
    }
    outputs = {
        "status": "delivery dispatch status - 'sent' or 'failed'",
        "sent": "boolean delivery result for easy downstream checks",
        "error": "error detail when status is 'failed'",
        "error_type": "stable failure class: validation_error, configuration_error, or delivery_error",
        "channel": "Buzz channel UUID used for delivery",
        "message_length": (
            "length of the message actually submitted for delivery "
            "(after normalization and 4096-char truncation)"
        ),
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        buzz = sources.get("buzz") or {}
        return bool(buzz.get("private_key"))

    # extract_params intentionally stays empty. It is serialized into tool-call
    # traces, so the Buzz private key must be resolved inside run() only.

    def run(
        self,
        message: str,
        channel: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        channel = normalize_optional_text(channel)
        valid, normalized_message, validation_error = validate_message(message)
        if not valid:
            return failed_result(
                available=True,
                error=validation_error,
                error_type="validation_error",
                channel=channel,
            )

        target, resolution_error = resolve_target(channel)
        if target is None:
            return failed_result(
                available=False,
                error=resolution_error,
                error_type="configuration_error",
                channel=channel,
                message_length=len(normalized_message),
            )

        ok, error = dispatch_message(normalized_message, target)
        if not ok:
            return failed_result(
                available=True,
                error=error,
                error_type="delivery_error",
                channel=target.channel,
                message_length=len(normalized_message),
            )
        return sent_result(target=target, message_length=len(normalized_message))


buzz_send_message = tool(
    BuzzSendMessageTool(),
    surfaces=(ToolSurface.ACTION,),
)
