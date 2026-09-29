"""Shared "edit one message in place" turn output for chat transports.

A chat turn shows a single message that starts as a status placeholder, is
edited in place while the turn runs, and holds the final answer. This base owns
that lifecycle — opening the placeholder, throttling edits by an interval,
normalizing status text, and releasing the message id on finalize so a later
session-goal turn posts a *fresh* message instead of overwriting an answer the
person already read.

Each transport supplies an :class:`MessageChannel` with the vendor I/O:
opening the placeholder, editing a preview, and delivering the final answer.
Transports that stream token-by-token (Slack) or split into several messages
with components (Discord) do not use this base.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable
from typing import Protocol

from infrastructure.turn_host.status_messages import (
    EMPTY_RESPONSE_MESSAGE,
    chat_status_headline,
    initial_status_message,
    status_from_response_label,
    user_facing_error_message,
)

logger = logging.getLogger("gateway")

_LOG_PREVIEW_LIMIT = 500


def log_preview(text: str) -> str:
    """One-line, length-capped rendering of outbound text for the server log."""
    preview = text.replace("\n", " ").strip()
    if len(preview) > _LOG_PREVIEW_LIMIT:
        return f"{preview[: _LOG_PREVIEW_LIMIT - 3]}..."
    return preview


class MessageChannel(Protocol):
    """Vendor I/O for one edit-in-place chat message.

    The base calls these; the implementation talks to the chat API and owns its
    own truncation, markup, and outbound logging.
    """

    #: Whether a status arriving after the id was released (a continuation turn)
    #: should open a new placeholder. Telegram does; Buzz waits for the next
    #: finalize to send a fresh message.
    reopen_placeholder_on_status: bool

    #: How this destination is named in server logs, e.g. ``"chat=123"`` — kept
    #: so concurrent-turn errors stay attributable to the failed destination.
    destination: str

    def on_status(self) -> None:
        """Signal turn activity before a status edit (e.g. a typing indicator)."""

    def open_placeholder(self, text: str) -> str:
        """Send ``text`` as a new message; return its id (``""`` on failure)."""

    def edit_preview(self, message_id: str, text: str) -> bool:
        """Edit ``message_id`` in place to ``text``; return whether it took."""

    def deliver_final(self, message_id: str, answer: str) -> str:
        """Deliver the final ``answer``; return the id to keep (``""`` once released)."""


class SingleMessageTurnOutput:
    """Turn output that edits one placeholder message in place.

    Wraps an :class:`MessageChannel`; exposes the ``TurnOutput`` surface the
    turn host writes to.
    """

    def __init__(
        self,
        *,
        channel: MessageChannel,
        edit_interval_seconds: float,
        tool_hooks: object | None = None,
    ) -> None:
        self.tool_hooks = tool_hooks
        # Set per turn by the transport's dispatcher; the turn runner reads it
        # to give tools a cooperative cancel signal on soft timeout or stop.
        self.turn_cancel: threading.Event | None = None
        self._channel = channel
        self._edit_interval = edit_interval_seconds
        self._last_edit = 0.0
        self._lock = threading.Lock()
        self._status_text = initial_status_message()
        self._channel.on_status()
        self._message_id = self._channel.open_placeholder(self._status_text)

    def print(self, message: str = "") -> None:
        if message:
            self._set_status(message)

    def render_response_header(self, label: str) -> None:
        self._set_status(status_from_response_label(label))

    def render_error(self, message: str) -> None:
        # Raw detail to the server log only; the user sees safe generic copy.
        logger.warning("gateway turn error %s: %s", self._channel.destination, message)
        self._finalize(user_facing_error_message(message))

    def set_tool_status(self, status: str) -> None:
        self._set_status(status)

    def finish_streamed_response(self, answer: str) -> None:
        self._finalize(answer or EMPTY_RESPONSE_MESSAGE)

    def finalize(self, answer: str) -> None:
        self._finalize(answer)

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        _ = (label, suppress_if_starts_with)
        parts: list[str] = []
        for chunk in chunks:
            parts.append(str(chunk))
            if time.monotonic() - self._last_edit >= self._edit_interval:
                self._edit_preview("".join(parts))
        text = "".join(parts)
        if defer_want_me_to_closer:
            return text
        self._finalize(text or EMPTY_RESPONSE_MESSAGE)
        return text

    def _set_status(self, status: str) -> None:
        self._status_text = chat_status_headline(status)
        self._channel.on_status()
        self._edit_preview(self._status_text)

    def _edit_preview(self, preview: str) -> None:
        with self._lock:
            text = preview or self._status_text
            if not self._message_id:
                # Prior answer was finalized (goal continuation): open a fresh
                # placeholder rather than editing the finished message.
                if self._channel.reopen_placeholder_on_status:
                    message_id = self._channel.open_placeholder(text)
                    if message_id:
                        self._message_id = message_id
                        self._last_edit = time.monotonic()
                return
            if self._channel.edit_preview(self._message_id, text):
                self._last_edit = time.monotonic()

    def _finalize(self, answer: str) -> None:
        # Release the id so the next session-goal turn cannot overwrite this
        # answer; the channel returns "" once it has delivered.
        self._message_id = self._channel.deliver_final(self._message_id, answer)
