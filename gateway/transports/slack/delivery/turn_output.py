"""Slack turn output: one reply, posted when the answer is final.

Progress is not a thread comment. While the turn runs, Slack shows its
loading status on the triggering thread (``assistant.threads.setStatus``)
and the dispatcher keeps an eyes reaction on the mention itself. The only
message posted is the finished answer.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable

from core.tool import ToolExecutionHooks
from gateway.transports.slack.client import (
    SLACK_MAX_MARKDOWN_BLOCK_CHARS,
    SLACK_MAX_MESSAGE_CHARS,
    Blocks,
    SlackMessagingClient,
)
from gateway.transports.slack.delivery.feedback import feedback_block
from infrastructure.text.markdown import tighten_markdown_emphasis
from infrastructure.text.truncation import truncate
from infrastructure.turn_host.status_messages import (
    EMPTY_RESPONSE_MESSAGE,
    chat_status_headline,
    status_from_response_label,
    user_facing_error_message,
)
from integrations.slack import markdown_to_slack_mrkdwn

logger = logging.getLogger("gateway")

# Slack drops an assistant status after two minutes if no message is sent.
# Refresh inside that window so a long turn keeps the indicator on the thread.
_LOADING_REFRESH_SECONDS = 45.0
_LOADING_STATUS = "is working on your request..."
_LOADING_DETAIL_MAX_CHARS = 150


class SlackTurnOutput:
    """Post the finished answer into the triggering Slack thread."""

    def __init__(
        self,
        *,
        client: SlackMessagingClient,
        channel_id: str,
        thread_ts: str,
        update_interval_seconds: float = 3.0,
        tool_hooks: ToolExecutionHooks | None = None,
    ) -> None:
        # Per-turn tool-execution hooks, read by TurnRunner. Slack leaves this
        # empty so write tools run without an Approve/Deny prompt.
        self.tool_hooks = tool_hooks
        # Set per turn by this transport's dispatcher; the turn runner reads it
        # to give tools a cooperative cancel signal on soft timeout or stop.
        self.turn_cancel: threading.Event | None = None
        _ = update_interval_seconds
        self._client = client
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._started_at = time.monotonic()
        self._lock = threading.Lock()
        self._loading_detail = _LOADING_STATUS
        self._loading_stop = threading.Event()
        self._show_loading()
        self._loading_thread = threading.Thread(
            target=self._refresh_loading,
            name="slack-turn-loading",
            daemon=True,
        )
        self._loading_thread.start()

    def print(self, message: str = "") -> None:
        if message:
            self._note_loading(message)

    def render_response_header(self, label: str) -> None:
        self._note_loading(status_from_response_label(label))

    def render_error(self, message: str) -> None:
        # Raw detail to the server log only; the user sees safe generic copy.
        logger.warning("gateway turn error channel=%s: %s", self._channel_id, message)
        self._finalize(user_facing_error_message(message))

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        _ = (label, suppress_if_starts_with)
        text = "".join(str(chunk) for chunk in chunks)
        if defer_want_me_to_closer:
            # Held until finish_streamed_response publishes the canonical text.
            return text
        return text if self._finalize(text or EMPTY_RESPONSE_MESSAGE) else ""

    def set_tool_status(self, status: str) -> None:
        # A Slack loading line is the label only. The argument row stays off Slack.
        self._note_loading(chat_status_headline(status))

    def finalize(self, answer: str) -> None:
        self._finalize(answer)

    def finish_streamed_response(self, answer: str) -> None:
        self._finalize(answer or EMPTY_RESPONSE_MESSAGE)

    def _note_loading(self, detail: str) -> None:
        line = " ".join(detail.split())
        if not line:
            return
        with self._lock:
            self._loading_detail = truncate(line, _LOADING_DETAIL_MAX_CHARS, suffix="…")
        self._show_loading()

    def _show_loading(self) -> None:
        setter = getattr(self._client, "set_thread_status", None)
        if not callable(setter):
            return
        with self._lock:
            detail = self._loading_detail
        messages = None if detail == _LOADING_STATUS else [detail]
        try:
            setter(
                channel=self._channel_id,
                thread_ts=self._thread_ts,
                status=_LOADING_STATUS,
                loading_messages=messages,
            )
        except Exception:
            logger.debug("[slack-turn-output] loading status failed", exc_info=True)

    def _refresh_loading(self) -> None:
        while not self._loading_stop.wait(_LOADING_REFRESH_SECONDS):
            self._show_loading()

    def _stop_loading(self) -> None:
        self._loading_stop.set()
        clearer = getattr(self._client, "set_thread_status", None)
        if not callable(clearer):
            return
        try:
            clearer(
                channel=self._channel_id,
                thread_ts=self._thread_ts,
                status="",
            )
        except Exception:
            logger.debug("[slack-turn-output] clear loading status failed", exc_info=True)

    def _finalize(self, answer: str) -> bool:
        final = truncate(markdown_to_slack_mrkdwn(answer), SLACK_MAX_MESSAGE_CHARS, suffix="…")
        blocks = self._final_blocks(answer)
        delivered = (
            self._client.post_message(
                channel=self._channel_id,
                text=final,
                thread_ts=self._thread_ts,
                blocks=blocks,
            )
            is not None
        )
        if delivered:
            logger.info(
                "outbound channel=%s thread_ts=%s mode=final chars=%d",
                self._channel_id,
                self._thread_ts,
                len(final),
            )
        else:
            logger.error(
                "[slack-turn-output] DELIVERY FAILED channel=%s thread_ts=%s chars=%d",
                self._channel_id,
                self._thread_ts,
                len(final),
            )
        self._stop_loading()
        return delivered

    def _final_blocks(self, answer: str) -> Blocks | None:
        """Compose the final reply: a ``markdown`` block + a context footer.

        Slack built the markdown block for LLM output: standard markdown
        (headers, tables, fenced code) renders natively instead of being
        mangled through mrkdwn. The context footer is the provenance line
        (who answered, how long it took) rendered in Slack's muted small type.
        Answers over the block's 12k-char limit stay text-only; the mrkdwn
        text is always sent alongside as the notification/fallback rendering.
        """
        body = tighten_markdown_emphasis(answer.strip())
        if not body or len(body) > SLACK_MAX_MARKDOWN_BLOCK_CHARS:
            return None
        return [{"type": "markdown", "text": body}, *self._closing_blocks()]

    def _closing_blocks(self) -> list[dict[str, object]]:
        """Provenance footer + 👍/👎 feedback buttons, on every final reply."""
        return [self._footer_block(), feedback_block()]

    def _footer_block(self) -> dict[str, object]:
        return {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": self._footer_text()}],
        }

    def _footer_text(self) -> str:
        return f"OpenSRE · AI-generated · {_format_duration(time.monotonic() - self._started_at)}"


def _format_duration(seconds: float) -> str:
    whole = max(0, int(seconds))
    if whole < 60:
        return f"{whole}s"
    return f"{whole // 60}m {whole % 60:02d}s"
