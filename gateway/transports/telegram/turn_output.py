"""Telegram turn output: edit one message in place, with a typing indicator."""

from __future__ import annotations

import logging

from gateway.core.single_message_output import (
    SingleMessageTurnOutput,
    log_preview,
)
from gateway.transports.telegram.poller.client import TelegramBotClient
from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.text.truncation import truncate
from integrations.telegram.delivery import truncate_for_telegram_html
from integrations.telegram.formatting import markdown_to_telegram_html

logger = logging.getLogger("gateway")


class _TelegramChannel:
    """Vendor I/O for a Telegram Bot API message edited in place."""

    reopen_placeholder_on_status = True

    def __init__(self, *, client: TelegramBotClient, chat_id: str) -> None:
        self._client = client
        self._chat_id = chat_id
        self.destination = f"chat={chat_id}"

    def on_status(self) -> None:
        self._client.send_chat_action(self._chat_id, "typing")

    def open_placeholder(self, text: str) -> str:
        ok, _, message_id = self._client.send_message(
            self._chat_id, truncate(text, MAX_MESSAGE_SIZE, suffix="…")
        )
        return message_id if ok else ""

    def edit_preview(self, message_id: str, text: str) -> bool:
        ok, _ = self._client.edit_message_text(
            self._chat_id, message_id, truncate(text, MAX_MESSAGE_SIZE, suffix="…")
        )
        return ok

    def deliver_final(self, message_id: str, answer: str) -> str:
        final = truncate(answer, MAX_MESSAGE_SIZE, suffix="…")
        # Render the answer's Markdown as Telegram HTML, falling back to the plain
        # answer if the API rejects the markup so a message is never lost.
        html_final = truncate_for_telegram_html(
            markdown_to_telegram_html(answer), MAX_MESSAGE_SIZE, suffix="…"
        )
        if message_id and self._edit_final(message_id, html_final, final):
            logger.info("outbound %s text=%r", self.destination, log_preview(final))
            return ""
        if self._send_final(html_final, final):
            logger.info("outbound %s text=%r", self.destination, log_preview(final))
            return ""
        return message_id

    def _edit_final(self, message_id: str, html_text: str, plain_text: str) -> bool:
        ok, _ = self._client.edit_message_text(
            self._chat_id, message_id, html_text, parse_mode="HTML"
        )
        if ok:
            return True
        ok, _ = self._client.edit_message_text(self._chat_id, message_id, plain_text)
        return ok

    def _send_final(self, html_text: str, plain_text: str) -> bool:
        ok, _, _ = self._client.send_message(self._chat_id, html_text, parse_mode="HTML")
        if ok:
            return True
        ok, _, _ = self._client.send_message(self._chat_id, plain_text)
        return ok


class TelegramTurnOutput(SingleMessageTurnOutput):
    """Stream assistant output back through the Telegram Bot API."""

    def __init__(
        self,
        *,
        client: TelegramBotClient,
        chat_id: str,
        edit_interval_seconds: float = 1.5,
        tool_hooks: object | None = None,
    ) -> None:
        super().__init__(
            channel=_TelegramChannel(client=client, chat_id=chat_id),
            edit_interval_seconds=edit_interval_seconds,
            tool_hooks=tool_hooks,
        )
