"""Discord sink must not leak exception detail to users (Wave D3)."""

from __future__ import annotations

from unittest.mock import patch

from gateway.transports.discord.turn_output import DiscordTurnOutput


def test_render_error_hides_raw_detail_behind_generic_copy() -> None:
    with (
        patch(
            "gateway.transports.discord.turn_output.send_message",
            return_value="msg-1",
        ),
        patch(
            "gateway.transports.discord.turn_output.edit_message_with_components"
        ) as edit_components,
    ):
        sink = DiscordTurnOutput(
            bot_token="tok",
            channel_id="ch-1",
            edit_interval_seconds=0.0,
        )
        sink.render_error("RuntimeError: token sk-DO-NOT-LEAK rejected by db-host:5432")

    assert edit_components.call_count == 1
    finalized = edit_components.call_args.kwargs["content"]
    assert "sk-DO-NOT-LEAK" not in finalized
    assert "db-host" not in finalized
    assert "Something went wrong" in finalized
