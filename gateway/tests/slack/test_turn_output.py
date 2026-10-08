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
    """Records the final post and the loading status on the triggering thread."""

    def __init__(self, *, post_ok: bool = True) -> None:
        self.post_ok = post_ok
        self.posts: list[dict[str, Any]] = []
        self.statuses: list[dict[str, Any]] = []

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
        _ = (channel, ts, text, blocks)
        return True

    def add_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        _ = (channel, timestamp, emoji)
        return True

    def remove_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        _ = (channel, timestamp, emoji)
        return True

    def delete_message(self, *, channel: str, ts: str) -> bool:
        _ = (channel, ts)
        return True

    def start_stream(self, *, channel: str, thread_ts: str) -> str | None:
        _ = (channel, thread_ts)
        return None

    def append_stream(self, *, channel: str, ts: str, chunks: Any) -> bool:
        _ = (channel, ts, chunks)
        return True

    def stop_stream(self, *, channel: str, ts: str, blocks: Any = None) -> bool:
        _ = (channel, ts, blocks)
        return True

    def set_thread_status(
        self,
        *,
        channel: str,
        thread_ts: str,
        status: str,
        loading_messages: Any = None,
    ) -> bool:
        self.statuses.append(
            {
                "channel": channel,
                "thread_ts": thread_ts,
                "status": status,
                "loading_messages": loading_messages,
            }
        )
        return True


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
    client = _FakeMessagingClient(post_ok=False)
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


def test_creation_shows_loading_on_the_thread_and_posts_nothing() -> None:
    client = _FakeMessagingClient()
    _sink(client)

    assert client.posts == []
    assert client.statuses[0]["thread_ts"] == "1700.100"
    assert client.statuses[0]["status"] == "is working on your request..."


def test_finalize_posts_the_answer_once() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("the root cause is a full disk")

    assert len(client.posts) == 1
    assert client.posts[0]["text"] == "the root cause is a full disk"
    assert client.posts[0]["thread_ts"] == "1700.100"
    assert client.statuses[-1]["status"] == ""


def test_finalize_truncates_oversized_text() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("x" * (SLACK_MAX_MESSAGE_CHARS + 1000))

    assert len(client.posts[-1]["text"]) <= SLACK_MAX_MESSAGE_CHARS


def test_stream_returns_full_text_and_updates_preview() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    text = sink.stream(label="assistant", chunks=["hello", " world"])

    assert text == "hello world"
    assert len(client.posts) == 1
    assert client.posts[0]["text"] == "hello world"


def test_empty_stream_posts_a_clear_fallback() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    text = sink.stream(label="assistant", chunks=[])

    assert text == ""
    assert client.posts[-1]["text"] == "I didn't have anything to add for that."


def test_finalize_sends_markdown_block_with_mrkdwn_fallback_text() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("## Root cause\nThe **disk** is full")

    final = client.posts[-1]
    # Native markdown block carries the original markdown untouched…
    assert final["blocks"][0] == {"type": "markdown", "text": "## Root cause\nThe **disk** is full"}
    # …while the text field stays mrkdwn for notifications/older clients.
    assert "disk" in final["text"]


def test_finalize_tightens_padded_bold_in_markdown_block() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("** I found: ** the disk is full")

    final = client.posts[-1]
    assert final["blocks"][0]["text"] == "**I found:** the disk is full"
    assert final["text"] == "*I found:* the disk is full"


def test_finalize_appends_provenance_footer() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("answer")

    footer = next(b for b in client.posts[-1]["blocks"] if b["type"] == "context")
    footer_text = footer["elements"][0]["text"]
    assert "OpenSRE" in footer_text
    assert "AI-generated" in footer_text


def test_finalize_appends_feedback_buttons_after_footer() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("answer")

    feedback = client.posts[-1]["blocks"][-1]
    assert feedback["type"] == "context_actions"
    element = feedback["elements"][0]
    assert element["type"] == "feedback_buttons"
    assert element["positive_button"]["value"] == "good"
    assert element["negative_button"]["value"] == "bad"


def test_tool_status_updates_loading_without_posting() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.set_tool_status("⏳ Run a local shell command…\n(echo sk-secret-token)")

    assert client.posts == []
    detail = client.statuses[-1]["loading_messages"][0]
    assert "sk-secret-token" not in detail
    assert "Run a local shell command" in detail


def test_finalize_skips_markdown_block_over_block_limit() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("x" * (SLACK_MAX_MARKDOWN_BLOCK_CHARS + 1))

    # Over the 12k block cap: text-only delivery, no rejected blocks payload.
    assert client.posts[-1]["blocks"] is None
    assert len(client.posts[-1]["text"]) > 0


def test_render_error_hides_raw_detail_behind_generic_copy() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.render_error("provider unavailable at db-host:5432")

    finalized = client.posts[-1]["text"]
    assert finalized == "Something went wrong handling that request. Please try again."
    assert "db-host" not in finalized


def test_status_then_answer_posts_only_the_answer() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.set_tool_status("working")
    sink.finalize("answer")

    assert len(client.posts) == 1
    assert client.posts[0]["text"] == "answer"
    assert client.posts[0]["thread_ts"] == "1700.100"


def test_stream_posts_one_final_message_with_footer() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.set_tool_status("Reading Slack messages")
    text = sink.stream(label="assistant", chunks=["The disk", " is full."])

    assert text == "The disk is full."
    assert len(client.posts) == 1
    assert client.posts[0]["text"] == "The disk is full."
    footer = next(b for b in client.posts[0]["blocks"] if b["type"] == "context")
    assert "AI-generated" in footer["elements"][0]["text"]
    assert client.posts[0]["blocks"][-1]["type"] == "context_actions"


def test_streamed_markdown_tightens_padded_bold() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.stream(label="assistant", chunks=["** I found: ** the disk is full"])

    assert client.posts[-1]["blocks"][0]["text"] == "**I found:** the disk is full"


def test_goal_continuation_posts_each_finished_answer() -> None:
    """A later outer-turn answer must not silently vanish.

    ``run_until_session_goal`` reuses one sink across iterations. Each finished
    answer is its own reply; nothing is posted before that answer is ready.
    """
    client = _FakeMessagingClient()
    sink = _sink(client)

    first = sink.stream(label="assistant", chunks=["step one"])
    second = sink.stream(label="assistant", chunks=["step two"])

    assert first == "step one"
    assert second == "step two"
    assert [post["text"] for post in client.posts] == ["step one", "step two"]


def test_finalize_after_stream_posts_the_continuation() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.stream(label="assistant", chunks=["step one"])
    sink.finalize("step two from finalize")

    assert [post["text"] for post in client.posts] == ["step one", "step two from finalize"]
