"""One turn's streamed Slack message — the ``chat.startStream`` state machine.

Separate from turn output: that class chooses a delivery path and renders
blocks; this owns stream lifecycle (start / append / stop), chunk throttling,
and the open-task bookkeeping the timeline surface needs.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from gateway.transports.slack.client import (
    SLACK_MAX_MARKDOWN_BLOCK_CHARS,
    Blocks,
    SlackMessagingClient,
)
from infrastructure.text.markdown import tighten_markdown_emphasis
from infrastructure.text.truncation import truncate

logger = logging.getLogger("gateway")


class TurnStream:
    """One turn's streamed Slack message (``chat.startStream`` lifecycle).

    Started lazily on the first tool status or answer chunk. Tool statuses
    become timeline ``task_update`` chunks (the previous task flips to
    ``complete`` when the next one starts); answer text streams as throttled
    ``markdown_text`` chunks. A start failure marks the stream dead for the
    turn and the sink stays on the placeholder path; an append failure after
    a successful start marks it broken and the sink re-delivers in full.
    """

    def __init__(
        self,
        *,
        client: SlackMessagingClient,
        channel_id: str,
        thread_ts: str,
        update_interval_seconds: float,
        on_started: Callable[[], None],
    ) -> None:
        self._client = client
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._update_interval = update_interval_seconds
        self._on_started = on_started
        self._ts: str | None = None
        # Successful stopStream — next ensure_started may open a fresh stream.
        self._closed = False
        # chat.startStream failed — stay on the placeholder path; do not re-probe.
        self._unavailable = False
        self._broken = False
        self._task_seq = 0
        self._open_task: tuple[str, str] | None = None  # (id, title)
        self._sent_text = ""
        self._pending_parts: list[str] = []
        self._last_flush = 0.0

    def _joined_pending(self) -> str:
        """Materialize buffered answer chunks (join once per flush)."""
        return "".join(self._pending_parts)

    @property
    def started(self) -> bool:
        return self._ts is not None

    @property
    def is_open(self) -> bool:
        """True while a stream is live and can still accept finish/append."""
        return (
            self._ts is not None and not self._closed and not self._broken and not self._unavailable
        )

    @property
    def dead(self) -> bool:
        """True after a successful stop (continuation may reopen) or a failed start."""
        return self._closed or self._unavailable

    @property
    def closed(self) -> bool:
        """True after a successful ``stopStream`` — safe to open a continuation stream."""
        return self._closed

    def ensure_started_for_continuation(self) -> bool:
        """Reset a finished stream and open a new one for the next outer turn."""
        return self._ensure_started()

    def note_task(self, title: str) -> bool:
        """Show ``title`` as the new in-progress timeline task."""
        if not self._ensure_started():
            return False
        chunks: list[dict[str, object]] = list(self._close_open_task_chunks())
        self._task_seq += 1
        task_id = f"task-{self._task_seq}"
        chunks.append(
            {"type": "task_update", "id": task_id, "title": title, "status": "in_progress"}
        )
        if not self._append(chunks):
            return False
        self._open_task = (task_id, title)
        return True

    def append_text(self, chunk: str) -> bool:
        """Buffer an answer chunk; flush on the update interval."""
        if self._broken or not self._ensure_started():
            return False
        self._pending_parts.append(chunk)
        if time.monotonic() - self._last_flush >= self._update_interval:
            self._flush_text()
        # Buffered content is delivered by finish() even if this flush failed.
        return not self._broken

    def finish(self, full_text: str, *, blocks: Blocks | None) -> bool:
        """Deliver any remaining text and stop the stream.

        Returns whether the streamed message contains the complete answer;
        on False the caller re-delivers ``full_text`` through the fallback
        path (the stream, if still open server-side, times out on its own).
        """
        if self._ts is None:
            return False
        if self._closed:
            # Already finished once (e.g. a timeout finalize raced the answer);
            # the streamed message stands as delivered.
            return True
        # Compare in tightened space: flushes already collapse ``** bold **``.
        full_text = tighten_markdown_emphasis(full_text)
        pending = tighten_markdown_emphasis(self._joined_pending())
        self._pending_parts.clear()
        if pending:
            self._pending_parts.append(pending)
        streamed = self._sent_text + pending
        if full_text.startswith(streamed):
            rest = full_text[len(streamed) :]
            if rest:
                self._pending_parts.append(rest)
        elif full_text != streamed:
            # A finalize with unrelated text (error copy, timeout notice)
            # lands after whatever partial answer already streamed.
            if streamed:
                self._pending_parts.append("\n\n")
            self._pending_parts.append(full_text)
        self._flush_text(include_task_close=True)
        stopped = self._client.stop_stream(channel=self._channel_id, ts=self._ts, blocks=blocks)
        if self._broken:
            return False
        if not stopped:
            # Content is fully appended; a failed stop only leaves the
            # streaming indicator until Slack expires it. Don't re-post.
            logger.warning(
                "[slack-turn-output] chat.stopStream failed channel=%s ts=%s",
                self._channel_id,
                self._ts,
            )
        self._closed = True
        return True

    def _ensure_started(self) -> bool:
        if self._broken or self._unavailable:
            return False
        if self._closed:
            # Prior response stopped (session goal continuation). Open a fresh
            # stream instead of treating later appends as already delivered.
            self._reset_for_continuation()
        if self._ts is not None:
            return True
        ts = self._client.start_stream(channel=self._channel_id, thread_ts=self._thread_ts)
        if ts is None:
            self._unavailable = True
            return False
        self._ts = ts
        self._last_flush = time.monotonic()
        self._on_started()
        return True

    def _reset_for_continuation(self) -> None:
        """Clear a finished stream so the next outer turn can start another."""
        self._ts = None
        self._closed = False
        self._broken = False
        self._task_seq = 0
        self._open_task = None
        self._sent_text = ""
        self._pending_parts.clear()
        self._last_flush = 0.0

    def _flush_text(self, *, include_task_close: bool = False) -> None:
        chunks: list[dict[str, object]] = []
        pending = self._joined_pending()
        if include_task_close or pending:
            # Answer text starting (or the turn ending) closes the open task.
            chunks.extend(self._close_open_task_chunks())
        if pending:
            budget = SLACK_MAX_MARKDOWN_BLOCK_CHARS - len(self._sent_text)
            flushed = tighten_markdown_emphasis(pending)
            text = truncate(flushed, max(budget, 1), suffix="…")
            chunks.append({"type": "markdown_text", "text": text})
            # Account for the logical tightened content even if Slack got a
            # truncated view at the markdown-block cap.
            self._sent_text += flushed
            self._pending_parts.clear()
        if chunks:
            self._append(chunks)

    def _close_open_task_chunks(self) -> list[dict[str, object]]:
        if self._open_task is None:
            return []
        (task_id, title), self._open_task = self._open_task, None
        return [{"type": "task_update", "id": task_id, "title": title, "status": "complete"}]

    def _append(self, chunks: list[dict[str, object]]) -> bool:
        if self._ts is None:
            return False
        if self._client.append_stream(channel=self._channel_id, ts=self._ts, chunks=chunks):
            self._last_flush = time.monotonic()
            return True
        self._broken = True
        return False


__all__ = ["TurnStream"]
