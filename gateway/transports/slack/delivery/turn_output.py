"""Slack turn output: streamed timeline reply with placeholder-edit fallback.

Preferred delivery is Slack's streaming surface (``chat.startStream`` →
``chat.appendStream`` → ``chat.stopStream``): tool progress renders as
timeline task cards and the answer streams as native markdown, like Claude
Tag. When streaming is unavailable (feature-gated workspace, old plan, API
error) this class falls back to the classic flow — one status placeholder
posted in-thread, edited in place while the turn runs, replaced by the final
answer.
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
from gateway.transports.slack.delivery.turn_stream import TurnStream
from infrastructure.text.markdown import tighten_markdown_emphasis
from infrastructure.text.truncation import truncate
from infrastructure.turn_host.status_messages import (
    EMPTY_RESPONSE_MESSAGE,
    chat_status_headline,
    initial_status_message,
    status_from_response_label,
    user_facing_error_message,
)
from integrations.slack import markdown_to_slack_mrkdwn

logger = logging.getLogger("gateway")


class SlackTurnOutput:
    """Stream assistant output back to the triggering Slack thread."""

    def __init__(
        self,
        *,
        client: SlackMessagingClient,
        channel_id: str,
        thread_ts: str,
        update_interval_seconds: float = 3.0,
        tool_hooks: ToolExecutionHooks | None = None,
    ) -> None:
        # Per-turn tool-execution hooks (e.g. the Block Kit approval gate),
        # read duck-typed by TurnRunner when building the agent.
        self.tool_hooks = tool_hooks
        # Set per turn by this transport's dispatcher; the turn runner reads it
        # to give tools a cooperative cancel signal on soft timeout or stop.
        self.turn_cancel: threading.Event | None = None
        self._client = client
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._update_interval = update_interval_seconds
        self._last_update = 0.0
        self._started_at = time.monotonic()
        # RLock: the turn stream's on-start callback deletes the placeholder
        # from inside an already-locked status/stream call.
        self._lock = threading.RLock()
        self._turn_stream = TurnStream(
            client=client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            update_interval_seconds=update_interval_seconds,
            on_started=self._drop_placeholder,
        )
        self._message_ts = client.post_message(
            channel=channel_id,
            text=_as_status_line(initial_status_message()),
            thread_ts=thread_ts,
        )
        if self._message_ts is None:
            logger.warning(
                "[slack-turn-output] placeholder post FAILED channel=%s thread_ts=%s; "
                "final answer will be posted as a new message",
                channel_id,
                thread_ts,
            )

    def print(self, message: str = "") -> None:
        if message:
            self._set_status(message)

    def render_response_header(self, label: str) -> None:
        self._set_status(status_from_response_label(label))

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
        parts: list[str] = []
        for chunk in chunks:
            text_chunk = str(chunk)
            parts.append(text_chunk)
            with self._lock:
                if self._turn_stream.append_text(text_chunk):
                    continue
            now = time.monotonic()
            if now - self._last_update >= self._update_interval:
                self._edit_preview("".join(parts))
        text = "".join(parts)
        if defer_want_me_to_closer:
            # Preview may show a drifted closer; finish_streamed_response
            # publishes the canonical rewrite after gather normalize.
            return text
        self._finalize(text or EMPTY_RESPONSE_MESSAGE)
        return text

    def set_tool_status(self, status: str) -> None:
        self._set_status(status)

    def finalize(self, answer: str) -> None:
        self._finalize(answer)

    def finish_streamed_response(self, answer: str) -> None:
        self._finalize(answer or EMPTY_RESPONSE_MESSAGE)

    def _set_status(self, status: str) -> None:
        # A Slack task title is the label row. The argument row stays on the shell.
        headline = chat_status_headline(status)
        with self._lock:
            if self._turn_stream.note_task(headline):
                return
        self._edit_preview(_as_status_line(headline))

    def _drop_placeholder(self) -> None:
        """The streamed message replaces the placeholder — remove it."""
        with self._lock:
            ts = self._message_ts
            self._message_ts = None
        if ts:
            self._client.delete_message(channel=self._channel_id, ts=ts)

    def _edit_preview(self, preview: str) -> None:
        if not self._message_ts:
            return
        preview = truncate(preview, SLACK_MAX_MESSAGE_CHARS, suffix="…")
        with self._lock:
            if self._message_ts and self._client.update_message(
                channel=self._channel_id, ts=self._message_ts, text=preview
            ):
                self._last_update = time.monotonic()

    def _finalize(self, answer: str) -> None:
        with self._lock:
            if self._turn_stream.is_open:
                if self._turn_stream.finish(answer, blocks=self._closing_blocks()):
                    logger.info(
                        "outbound channel=%s thread_ts=%s mode=stream chars=%d",
                        self._channel_id,
                        self._thread_ts,
                        len(answer),
                    )
                    return
                # Stream broke mid-turn: deliver the full answer the classic way.
                logger.warning(
                    "[slack-turn-output] stream delivery failed channel=%s thread_ts=%s; "
                    "falling back to a plain message",
                    self._channel_id,
                    self._thread_ts,
                )
            elif self._turn_stream.closed:
                # Prior stopStream (session goal continuation, or a raced finalize).
                # Open a fresh stream so later-turn answer is not treated as already
                # delivered; if start fails, fall through to classic post.
                if self._turn_stream.ensure_started_for_continuation() and self._turn_stream.finish(
                    answer, blocks=self._closing_blocks()
                ):
                    logger.info(
                        "outbound channel=%s thread_ts=%s mode=stream-continuation chars=%d",
                        self._channel_id,
                        self._thread_ts,
                        len(answer),
                    )
                    return
        final = truncate(markdown_to_slack_mrkdwn(answer), SLACK_MAX_MESSAGE_CHARS, suffix="…")
        blocks = self._final_blocks(answer)
        mode = "edit"
        with self._lock:
            delivered = self._message_ts is not None and self._client.update_message(
                channel=self._channel_id, ts=self._message_ts, text=final, blocks=blocks
            )
            if not delivered:
                mode = "new-message"
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
                "outbound channel=%s thread_ts=%s mode=%s chars=%d",
                self._channel_id,
                self._thread_ts,
                mode,
                len(final),
            )
        else:
            # Both the in-place edit and the fresh post failed: the user is left
            # staring at the "Digging in…" placeholder with no answer.
            logger.error(
                "[slack-turn-output] DELIVERY FAILED channel=%s thread_ts=%s chars=%d "
                "(both update and post rejected)",
                self._channel_id,
                self._thread_ts,
                len(final),
            )

    def _final_blocks(self, answer: str) -> Blocks | None:
        """Compose the final reply: a ``markdown`` block + a context footer.

        Slack built the markdown block for LLM output: standard markdown
        (headers, tables, fenced code) renders natively instead of being
        mangled through mrkdwn. The context footer is the Claude-Tag-style
        provenance line (who answered, how long it took) rendered in Slack's
        muted small type. Answers over the block's 12k-char limit stay
        text-only; the mrkdwn text is always sent alongside as the
        notification/fallback rendering.
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


def _as_status_line(status: str) -> str:
    """Render an in-progress status as one italic mrkdwn line.

    Mirrors the "is thinking…" affordance in Claude Tag / Slack assistant
    threads: progress reads as muted meta-status, clearly distinct from the
    final answer that replaces it.
    """
    line = " ".join(status.split())
    return f"_{line}_" if line else line
