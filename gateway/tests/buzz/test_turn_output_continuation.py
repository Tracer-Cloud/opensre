"""Buzz sink must not overwrite an earlier goal turn's answer."""

from __future__ import annotations

from unittest.mock import MagicMock

from gateway.transports.buzz.turn_output import BuzzTurnOutput


def _client() -> MagicMock:
    client = MagicMock()
    client.send_message.return_value = {"success": True, "event_id": "e1"}
    client.edit_message.return_value = {"success": True}
    return client


def test_a_second_goal_turn_sends_a_new_message() -> None:
    """Session-goal continuation runs several turns through one sink.

    Turn one replaces the placeholder by editing it; turn two must be its own
    message. Editing the same event again silently replaces an answer the user
    has already read, so a five-step goal would show only step five.
    """
    # Arrange.
    client = _client()
    sink = BuzzTurnOutput(client=client, channel_id="c1", edit_interval_seconds=0.0)
    sends_after_placeholder = client.send_message.call_count

    # Act.
    sink.finalize("turn one answer")
    sink.finalize("turn two answer")

    # Assert: one edit for turn one, a fresh send for turn two.
    assert client.edit_message.call_count == 1
    assert client.send_message.call_count == sends_after_placeholder + 1
    assert "turn two answer" in client.send_message.call_args.kwargs["content"]
