"""Direct contract tests for the shared edit-in-place turn output base.

A recording fake channel pins the state machine the base owns — placeholder
open, in-place edits, and the id-release-on-finalize that makes a later
session-goal turn post a fresh message instead of overwriting a delivered
answer — independently of any transport's vendor I/O.
"""

from __future__ import annotations

import logging

from gateway.core.single_message_output import SingleMessageTurnOutput


class _FakeChannel:
    """Records every base -> channel call and hands out predictable ids."""

    def __init__(self, *, reopen_placeholder_on_status: bool) -> None:
        self.reopen_placeholder_on_status = reopen_placeholder_on_status
        self.destination = "chat=42"
        self.calls: list[tuple[str, ...]] = []
        self._ids = iter(["m1", "m2", "m3"])

    def on_status(self) -> None:
        self.calls.append(("on_status",))

    def open_placeholder(self, text: str) -> str:
        self.calls.append(("open", text))
        return next(self._ids)

    def edit_preview(self, message_id: str, text: str) -> bool:
        self.calls.append(("edit", message_id, text))
        return True

    def deliver_final(self, message_id: str, answer: str) -> str:
        self.calls.append(("final", message_id, answer))
        return ""  # every real channel releases the id once delivered


def _output(*, reopen: bool) -> tuple[SingleMessageTurnOutput, _FakeChannel]:
    channel = _FakeChannel(reopen_placeholder_on_status=reopen)
    return (
        SingleMessageTurnOutput(channel=channel, edit_interval_seconds=0.0),
        channel,
    )


def test_construction_signals_activity_then_opens_the_placeholder() -> None:
    # Act
    output, channel = _output(reopen=True)

    # Assert: activity signal precedes the placeholder open (opened with a
    # non-empty status), and the returned id is retained.
    assert channel.calls[0] == ("on_status",)
    assert channel.calls[1][0] == "open" and channel.calls[1][1]
    assert output._message_id == "m1"


def test_status_preview_omits_the_argument_row() -> None:
    output, channel = _output(reopen=True)

    output.set_tool_status("⏳ Run a local shell command…\n(echo sk-secret-token)")

    edited = [call for call in channel.calls if call[0] == "edit"]
    assert edited
    assert "sk-secret-token" not in edited[-1][2]
    assert "Run a local shell command" in edited[-1][2]


def test_finalize_releases_the_id_and_reopen_channel_posts_a_fresh_message() -> None:
    # Arrange
    output, channel = _output(reopen=True)

    # Act: finish one turn, then a second turn's status arrives on the same sink.
    output.finalize("turn one")
    output.set_tool_status("thinking")

    # Assert: the first answer was delivered against the opened id, then the id
    # was released so the status opened a brand-new placeholder (not an edit of
    # the delivered answer).
    assert ("final", "m1", "turn one") in channel.calls
    assert ("open", "thinking") in channel.calls
    assert output._message_id == "m2"


def test_non_reopen_channel_waits_for_the_next_finalize() -> None:
    # Arrange
    output, channel = _output(reopen=False)

    # Act
    output.finalize("turn one")
    output.set_tool_status("thinking")  # no id and no reopen -> nothing sent
    output.finalize("turn two")

    # Assert: no placeholder was reopened for the mid-continuation status; the
    # next answer is delivered fresh with the id already released ("").
    assert ("open", "thinking") not in channel.calls
    assert ("final", "", "turn two") in channel.calls


def test_stream_finalizes_unless_deferred() -> None:
    # Arrange
    output, channel = _output(reopen=True)

    # Act
    text = output.stream(label="assistant", chunks=["he", "llo"])

    # Assert
    assert text == "hello"
    assert any(call[0] == "final" for call in channel.calls)


def test_deferred_stream_returns_text_without_finalizing() -> None:
    # Arrange
    output, channel = _output(reopen=True)

    # Act
    text = output.stream(label="assistant", chunks=["hi"], defer_want_me_to_closer=True)

    # Assert
    assert text == "hi"
    assert not any(call[0] == "final" for call in channel.calls)


def test_render_error_logs_the_failed_destination(caplog) -> None:
    # Arrange
    output, channel = _output(reopen=True)

    # Act
    with caplog.at_level(logging.WARNING, logger="gateway"):
        output.render_error("boom: secret detail")

    # Assert: the server-side warning names the destination so concurrent-turn
    # errors stay attributable; the user-facing answer is generic, never the raw
    # detail.
    assert "chat=42" in caplog.text
    final_calls = [call for call in channel.calls if call[0] == "final"]
    assert final_calls and "secret detail" not in final_calls[-1][2]
