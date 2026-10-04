from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from core.agent_harness.turns.action_driver import _deferred_reply_presenter, _show_response
from core.agent_harness.turns.skill_value import record_skill_value
from gateway.transports.slack.client import (
    SLACK_MAX_MARKDOWN_BLOCK_CHARS,
    SLACK_MAX_MESSAGE_CHARS,
)
from gateway.transports.slack.delivery.turn_output import (
    SlackTurnOutput,
)


class _FakeMessagingClient:
    """Records posts/updates; per-instance switches simulate API failures.

    ``stream_ok=False`` (the default) mimics a workspace where
    ``chat.startStream`` is unavailable, so most tests exercise the
    placeholder-edit fallback path.
    """

    def __init__(
        self,
        *,
        post_ok: bool = True,
        update_ok: bool = True,
        stream_ok: bool = False,
        append_ok: bool = True,
    ) -> None:
        self.post_ok = post_ok
        self.update_ok = update_ok
        self.stream_ok = stream_ok
        self.append_ok = append_ok
        self.posts: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []
        self.stream_starts: list[dict[str, Any]] = []
        self.stream_appends: list[dict[str, Any]] = []
        self.stream_stops: list[dict[str, Any]] = []

    def post_message(
        self,
        *,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: Any = None,
    ) -> str | None:
        self.posts.append(
            {"channel": channel, "text": text, "thread_ts": thread_ts, "blocks": blocks}
        )
        return f"ts-{len(self.posts)}" if self.post_ok else None

    def update_message(self, *, channel: str, ts: str, text: str, blocks: Any = None) -> bool:
        self.updates.append({"channel": channel, "ts": ts, "text": text, "blocks": blocks})
        return self.update_ok

    def add_reaction(self, **_kwargs: Any) -> bool:
        return True

    def remove_reaction(self, **_kwargs: Any) -> bool:
        return True

    def delete_message(self, *, channel: str, ts: str) -> bool:
        self.deletes.append({"channel": channel, "ts": ts})
        return True

    def start_stream(self, *, channel: str, thread_ts: str) -> str | None:
        self.stream_starts.append({"channel": channel, "thread_ts": thread_ts})
        return f"stream-{len(self.stream_starts)}" if self.stream_ok else None

    def append_stream(self, *, channel: str, ts: str, chunks: Any) -> bool:
        self.stream_appends.append({"channel": channel, "ts": ts, "chunks": list(chunks)})
        return self.append_ok

    def stop_stream(self, *, channel: str, ts: str, blocks: Any = None) -> bool:
        self.stream_stops.append({"channel": channel, "ts": ts, "blocks": blocks})
        return True

    def all_streamed_chunks(self) -> list[dict[str, Any]]:
        return [chunk for append in self.stream_appends for chunk in append["chunks"]]


def _sink(client: _FakeMessagingClient) -> SlackTurnOutput:
    return SlackTurnOutput(
        client=client,
        channel_id="C222",
        thread_ts="1700.100",
        update_interval_seconds=0.0,
    )


def test_failed_slack_delivery_does_not_credit_skill_insight() -> None:
    report = "What insights stand out:\n- CI failures account for 0.5% of PR runs."
    session = SimpleNamespace(active_skill="analyzing-github-ci-performance")
    client = _FakeMessagingClient(post_ok=False, update_ok=False)
    sink = _sink(client)
    with patch("core.agent_harness.turns.skill_value.capture_skill_value_delivered") as capture:
        replies: list[str] = []
        present = _deferred_reply_presenter(
            sink, replies, lambda text: record_skill_value(session, text, set())
        )
        assert not present(report)
        assert replies == []
        assert _show_response(sink, handled=True, final_text=report, display_chunks=[report]) == ""
        capture.assert_not_called()

        client.post_ok = True
        assert present(report)
        assert capture.call_count == 1


def test_posts_status_placeholder_into_thread_on_creation() -> None:
    client = _FakeMessagingClient()
    _sink(client)

    assert len(client.posts) == 1
    assert client.posts[0]["thread_ts"] == "1700.100"
    assert client.posts[0]["text"]


def test_finalize_replaces_placeholder_with_answer() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("the root cause is a full disk")

    assert client.updates[-1]["ts"] == "ts-1"
    assert client.updates[-1]["text"] == "the root cause is a full disk"
    assert len(client.posts) == 1


def test_finalize_posts_new_message_when_update_fails() -> None:
    client = _FakeMessagingClient(update_ok=False)
    sink = _sink(client)

    sink.finalize("answer")

    assert client.posts[-1]["text"] == "answer"
    assert client.posts[-1]["thread_ts"] == "1700.100"


def test_finalize_truncates_oversized_text() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("x" * (SLACK_MAX_MESSAGE_CHARS + 1000))

    assert len(client.updates[-1]["text"]) <= SLACK_MAX_MESSAGE_CHARS


def test_stream_returns_full_text_and_updates_preview() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    text = sink.stream(label="assistant", chunks=["hello", " world"])

    assert text == "hello world"
    assert client.updates[-1]["text"] == "hello world"


def test_empty_stream_finalizes_with_placeholder_fallback() -> None:
    # Arrange
    client = _FakeMessagingClient()
    sink = _sink(client)

    # Act: a turn that streams nothing at all.
    text = sink.stream(label="assistant", chunks=[])

    # Assert: the placeholder is replaced with a clear message, not left blank.
    assert text == ""
    assert client.updates[-1]["text"] == "I didn't have anything to add for that."


def test_finalize_sends_markdown_block_with_mrkdwn_fallback_text() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("## Root cause\nThe **disk** is full")

    final = client.updates[-1]
    # Native markdown block carries the original markdown untouched…
    assert final["blocks"][0] == {"type": "markdown", "text": "## Root cause\nThe **disk** is full"}
    # …while the text field stays mrkdwn for notifications/older clients.
    assert "disk" in final["text"]


def test_finalize_tightens_padded_bold_in_markdown_block() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("** I found: ** the disk is full")

    final = client.updates[-1]
    assert final["blocks"][0]["text"] == "**I found:** the disk is full"
    assert final["text"] == "*I found:* the disk is full"


def test_finalize_appends_provenance_footer() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("answer")

    footer = next(b for b in client.updates[-1]["blocks"] if b["type"] == "context")
    footer_text = footer["elements"][0]["text"]
    assert "OpenSRE" in footer_text
    assert "AI-generated" in footer_text


def test_finalize_appends_feedback_buttons_after_footer() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("answer")

    feedback = client.updates[-1]["blocks"][-1]
    assert feedback["type"] == "context_actions"
    element = feedback["elements"][0]
    assert element["type"] == "feedback_buttons"
    assert element["positive_button"]["value"] == "good"
    assert element["negative_button"]["value"] == "bad"


def test_status_updates_omit_the_argument_row() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.set_tool_status("⏳ Run a local shell command…\n(echo sk-secret-token)")

    status = client.updates[-1]["text"]
    assert "sk-secret-token" not in status
    assert "Run a local shell command" in status


def test_status_updates_render_as_italic_meta_text() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.set_tool_status("Running kubectl get pods")

    status = client.updates[-1]["text"]
    assert status.startswith("_") and status.endswith("_")


def test_finalize_skips_markdown_block_over_block_limit() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("x" * (SLACK_MAX_MARKDOWN_BLOCK_CHARS + 1))

    # Over the 12k block cap: text-only delivery, no rejected blocks payload.
    assert client.updates[-1]["blocks"] is None
    assert len(client.updates[-1]["text"]) > 0


def test_status_updates_never_carry_blocks() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.set_tool_status("Running kubectl get pods")

    assert client.updates[-1]["blocks"] is None


def test_tool_status_edits_placeholder() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.set_tool_status("Running kubectl get pods")

    assert client.updates
    assert "kubectl" in client.updates[-1]["text"]


def test_render_error_hides_raw_detail_behind_generic_copy() -> None:
    # Arrange
    client = _FakeMessagingClient()
    sink = _sink(client)

    # Act: hand render_error a raw exception string with sensitive detail.
    sink.render_error("provider unavailable at db-host:5432")

    # Assert: the thread shows generic copy, none of the raw detail.
    finalized = client.updates[-1]["text"]
    assert finalized == "Something went wrong handling that request. Please try again."
    assert "db-host" not in finalized


def test_survives_failed_placeholder_post() -> None:
    client = _FakeMessagingClient(post_ok=False)
    sink = _sink(client)
    client.post_ok = True

    sink.set_tool_status("working")
    sink.finalize("answer")

    # No placeholder to edit: statuses are dropped, the answer is posted fresh.
    assert not client.updates
    assert client.posts[-1]["text"] == "answer"


# ---------------------------------------------------------------------------
# Streamed delivery (chat.startStream / appendStream / stopStream)
# ---------------------------------------------------------------------------


def test_streaming_turn_renders_tasks_then_markdown_then_stops_with_footer() -> None:
    client = _FakeMessagingClient(stream_ok=True)
    sink = _sink(client)

    sink.set_tool_status("Reading Slack messages")
    sink.set_tool_status("Checking Kubernetes pods")
    text = sink.stream(label="assistant", chunks=["The disk", " is full."])
    assert text == "The disk is full."

    # The placeholder is replaced by the streamed message.
    assert client.deletes and client.deletes[0]["ts"] == "ts-1"
    assert len(client.stream_starts) == 1

    chunks = client.all_streamed_chunks()
    task_chunks = [c for c in chunks if c["type"] == "task_update"]
    # Two tasks; the first completes when the second starts, the second when
    # the answer text begins.
    assert [c["status"] for c in task_chunks] == [
        "in_progress",
        "complete",
        "in_progress",
        "complete",
    ]
    assert "Reading Slack messages" in task_chunks[0]["title"]

    markdown = "".join(c["text"] for c in chunks if c["type"] == "markdown_text")
    assert markdown == "The disk is full."

    # Stopped once, closing with the provenance footer + feedback buttons.
    assert len(client.stream_stops) == 1
    stop_blocks = client.stream_stops[0]["blocks"]
    footer = next(b for b in stop_blocks if b["type"] == "context")
    assert "AI-generated" in footer["elements"][0]["text"]
    assert stop_blocks[-1]["type"] == "context_actions"
    # The answer was fully streamed: no legacy edit/post delivery on top.
    assert all("answer" not in update["text"] for update in client.updates)
    assert len(client.posts) == 1  # just the placeholder


def test_streamed_markdown_tightens_padded_bold() -> None:
    client = _FakeMessagingClient(stream_ok=True)
    sink = _sink(client)

    sink.stream(label="assistant", chunks=["** I found: ** the disk is full"])

    markdown = "".join(
        c["text"] for c in client.all_streamed_chunks() if c["type"] == "markdown_text"
    )
    assert markdown == "**I found:** the disk is full"


def test_stream_start_failure_is_probed_once_then_placeholder_edits() -> None:
    client = _FakeMessagingClient(stream_ok=False)
    sink = _sink(client)

    sink.set_tool_status("step one")
    sink.set_tool_status("step two")

    # One probe only; both statuses land as placeholder edits.
    assert len(client.stream_starts) == 1
    assert not client.deletes
    assert len(client.updates) == 2


def test_stream_append_failure_falls_back_to_full_redelivery() -> None:
    client = _FakeMessagingClient(stream_ok=True, append_ok=False)
    sink = _sink(client)

    sink.set_tool_status("working")  # starts stream, append fails -> broken
    sink.finalize("the full answer")

    # The placeholder was already deleted for the stream, so the fallback
    # posts the complete answer as a fresh message.
    assert client.posts[-1]["text"] == "the full answer"
    assert client.posts[-1]["thread_ts"] == "1700.100"


def test_finalize_after_streamed_answer_does_not_duplicate_text() -> None:
    client = _FakeMessagingClient(stream_ok=True)
    sink = _sink(client)

    sink.stream(label="assistant", chunks=["done"])

    markdown = "".join(
        c["text"] for c in client.all_streamed_chunks() if c["type"] == "markdown_text"
    )
    assert markdown == "done"
    assert len(client.stream_stops) == 1


def test_goal_continuation_delivers_a_second_streamed_answer() -> None:
    """After stopStream, a later outer-turn answer must not silently vanish.

    ``run_until_session_goal`` reuses one sink across iterations. The first
    turn stops the Slack stream; a second ``stream``/``finalize`` must open a
    fresh stream (or post) so continuation text reaches the thread.
    """
    client = _FakeMessagingClient(stream_ok=True)
    sink = _sink(client)

    first = sink.stream(label="assistant", chunks=["step one"])
    second = sink.stream(label="assistant", chunks=["step two"])

    assert first == "step one"
    assert second == "step two"
    assert len(client.stream_starts) == 2
    assert len(client.stream_stops) == 2
    markdown = "".join(
        c["text"] for c in client.all_streamed_chunks() if c["type"] == "markdown_text"
    )
    assert "step one" in markdown
    assert "step two" in markdown


def test_finalize_after_finished_stream_posts_continuation_text() -> None:
    """A finalize-only continuation after stopStream must not claim success and drop."""
    client = _FakeMessagingClient(stream_ok=True)
    sink = _sink(client)

    sink.stream(label="assistant", chunks=["step one"])
    sink.finalize("step two from finalize")

    markdown = "".join(
        c["text"] for c in client.all_streamed_chunks() if c["type"] == "markdown_text"
    )
    posted = " ".join(post["text"] for post in client.posts)
    assert "step two from finalize" in markdown or "step two from finalize" in posted
    assert len(client.stream_starts) >= 2 or "step two from finalize" in posted


def test_error_after_partial_stream_appends_error_copy() -> None:
    client = _FakeMessagingClient(stream_ok=True)
    sink = _sink(client)

    sink.set_tool_status("working")
    sink.render_error("provider exploded at db-host:5432")

    markdown = "".join(
        c["text"] for c in client.all_streamed_chunks() if c["type"] == "markdown_text"
    )
    assert "Something went wrong" in markdown
    assert "db-host" not in markdown
    assert len(client.stream_stops) == 1
