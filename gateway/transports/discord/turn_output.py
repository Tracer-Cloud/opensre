"""Discord turn output."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable

from gateway.transports.discord.client import (
    edit_message,
    edit_message_with_components,
    send_message,
    send_message_with_components,
    split_discord_content,
)
from gateway.transports.discord.feedback import feedback_components
from infrastructure.text.markdown import tighten_markdown_emphasis
from infrastructure.turn_host.status_messages import (
    EMPTY_RESPONSE_MESSAGE,
    chat_status_headline,
    initial_status_message,
    status_from_response_label,
    user_facing_error_message,
)

logger = logging.getLogger("gateway")


class DiscordTurnOutput:
    """Stream assistant output back to a Discord channel or thread."""

    def __init__(
        self,
        *,
        bot_token: str,
        channel_id: str,
        edit_interval_seconds: float = 2.0,
        tool_hooks: object | None = None,
    ) -> None:
        self.tool_hooks = tool_hooks
        # Set per turn by this transport's dispatcher; the turn runner reads it
        # to give tools a cooperative cancel signal on soft timeout or stop.
        self.turn_cancel: threading.Event | None = None
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._edit_interval = edit_interval_seconds
        self._lock = threading.RLock()
        self._last_edit = 0.0
        self._message_id = send_message(
            channel_id=channel_id,
            content=f"*{initial_status_message()}*",
            bot_token=bot_token,
        )

    def print(self, message: str = "") -> None:
        if message:
            self._set_status(message)

    def render_response_header(self, label: str) -> None:
        self._set_status(status_from_response_label(label))

    def render_error(self, message: str) -> None:
        logger.warning("gateway turn error channel=%s: %s", self._channel_id, message)
        self.finalize(user_facing_error_message(message))

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        _ = (label, suppress_if_starts_with, defer_want_me_to_closer)
        parts: list[str] = []
        for chunk in chunks:
            parts.append(str(chunk))
            combined = "".join(parts)
            now = time.monotonic()
            if now - self._last_edit >= self._edit_interval:
                self._edit_preview(combined)
        return "".join(parts)

    def set_tool_status(self, status: str) -> None:
        self._set_status(status)

    def finish_streamed_response(self, answer: str) -> None:
        self.finalize(answer)

    def finalize(self, answer: str) -> None:
        body = tighten_markdown_emphasis((answer or EMPTY_RESPONSE_MESSAGE).strip())
        chunks = split_discord_content(body)
        if not chunks:
            return
        with self._lock:
            # Release the placeholder so a later session-goal turn posts a new
            # message instead of overwriting the answer already delivered.
            message_id = self._message_id
            self._message_id = ""
            if not message_id:
                # Continuation turn: nothing to edit, so deliver fresh messages.
                for extra in chunks[:-1]:
                    send_message(
                        channel_id=self._channel_id,
                        content=extra,
                        bot_token=self._bot_token,
                    )
                send_message_with_components(
                    channel_id=self._channel_id,
                    content=chunks[-1],
                    components=feedback_components(),
                    bot_token=self._bot_token,
                )
                return
            if len(chunks) == 1:
                edit_message_with_components(
                    channel_id=self._channel_id,
                    message_id=message_id,
                    content=chunks[0],
                    components=feedback_components(),
                    bot_token=self._bot_token,
                )
                return
            edit_message(
                channel_id=self._channel_id,
                message_id=message_id,
                content=chunks[0],
                bot_token=self._bot_token,
            )
            for extra in chunks[1:-1]:
                send_message(
                    channel_id=self._channel_id,
                    content=extra,
                    bot_token=self._bot_token,
                )
            send_message_with_components(
                channel_id=self._channel_id,
                content=chunks[-1],
                components=feedback_components(),
                bot_token=self._bot_token,
            )

    def _set_status(self, status: str) -> None:
        # Discord italics stay on the label row. The argument row stays on the shell.
        self._edit_preview(f"*{chat_status_headline(status)}*")

    def _edit_preview(self, preview: str) -> None:
        with self._lock:
            if not self._message_id:
                self._message_id = send_message(
                    channel_id=self._channel_id,
                    content=preview[:2000],
                    bot_token=self._bot_token,
                )
                return
            now = time.monotonic()
            if now - self._last_edit < self._edit_interval:
                return
            if edit_message(
                channel_id=self._channel_id,
                message_id=self._message_id,
                content=preview[:2000],
                bot_token=self._bot_token,
            ):
                self._last_edit = now
