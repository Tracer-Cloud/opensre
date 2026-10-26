"""Buzz turn output: edit one message in place.

Buzz content is already markdown (Desktop renders it directly), so no HTML
transform is needed on the way out, and Buzz posts no typing indicator. Status
updates edit one stored event in place via ``buzz messages edit`` (confirmed
safe for repeated edits), matching Telegram's/Discord's UX.
"""

from __future__ import annotations

import logging

from gateway.core.single_message_output import (
    SingleMessageTurnOutput,
    log_preview,
)
from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.text.truncation import truncate
from integrations.buzz import BuzzClient

logger = logging.getLogger("gateway")


class _BuzzChannel:
    """Vendor I/O for a Buzz channel message edited in place."""

    # A status arriving after finalize waits for the next finalize to send a
    # fresh message, rather than reopening a placeholder mid-continuation.
    reopen_placeholder_on_status = False

    def __init__(self, *, client: BuzzClient, channel_id: str) -> None:
        self._client = client
        self._channel_id = channel_id
        self.destination = f"channel={channel_id}"

    def on_status(self) -> None:
        # Buzz has no typing indicator.
        return

    def open_placeholder(self, text: str) -> str:
        result = self._client.send_message(
            channel=self._channel_id, content=truncate(text, MAX_MESSAGE_SIZE, suffix="…")
        )
        return result["event_id"] if result["success"] else ""

    def edit_preview(self, message_id: str, text: str) -> bool:
        result = self._client.edit_message(
            event_id=message_id, content=truncate(text, MAX_MESSAGE_SIZE, suffix="…")
        )
        return bool(result["success"])

    def deliver_final(self, message_id: str, answer: str) -> str:
        final = truncate(answer, MAX_MESSAGE_SIZE, suffix="…")
        if message_id and self._edit_final(message_id, final):
            logger.info("outbound %s text=%r", self.destination, log_preview(final))
            return ""
        if self._send_final(final):
            logger.info("outbound %s text=%r", self.destination, log_preview(final))
            return ""
        return message_id

    def _edit_final(self, message_id: str, answer: str) -> bool:
        result = self._client.edit_message(event_id=message_id, content=answer)
        return bool(result["success"])

    def _send_final(self, answer: str) -> bool:
        result = self._client.send_message(channel=self._channel_id, content=answer)
        return bool(result["success"])


class BuzzTurnOutput(SingleMessageTurnOutput):
    """Stream assistant output back through the Buzz channel."""

    def __init__(
        self,
        *,
        client: BuzzClient,
        channel_id: str,
        edit_interval_seconds: float = 1.5,
        tool_hooks: object | None = None,
    ) -> None:
        super().__init__(
            channel=_BuzzChannel(client=client, channel_id=channel_id),
            edit_interval_seconds=edit_interval_seconds,
            tool_hooks=tool_hooks,
        )


__all__ = ["BuzzTurnOutput"]
