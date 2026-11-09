from __future__ import annotations

import logging
from unittest.mock import MagicMock

from gateway.transports.telegram.poller.client import TelegramBotClient
from gateway.transports.telegram.turn_output import TelegramTurnOutput
from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE


def test_initial_status_is_not_working_placeholder() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")

    TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)

    sent = client.send_message.call_args.args[1]
    assert sent != "Working…"
    assert sent.endswith("…")
    client.send_chat_action.assert_called_with("123", "typing")


def test_set_status_never_shows_working_placeholder() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.set_tool_status("Working…")

    edited = client.edit_message_text.call_args.args[2]
    assert edited != "Working…"
    assert not edited.startswith("Working")


def test_render_response_header_uses_friendly_assistant_status() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.render_response_header("assistant")

    edited = client.edit_message_text.call_args.args[2]
    assert edited == "💬 Composing your reply…"


def test_stream_throttles_edits() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=10.0)
    text = sink.stream(label="assistant", chunks=["hello", " world"])
    assert text == "hello world"
    assert client.edit_message_text.call_count >= 1


def test_finalize_truncates_long_text() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)
    sink.finalize("x" * 5000)
    edited = client.edit_message_text.call_args[0][2]
    assert len(edited) <= MAX_MESSAGE_SIZE


def test_finalize_logs_outbound_edited_message(caplog) -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)

    with caplog.at_level(logging.INFO, logger="gateway"):
        sink.finalize("hello\nteam")

    assert "outbound chat=123 text='hello team'" in caplog.text


def test_finalize_logs_outbound_fallback_send(caplog) -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.side_effect = [(True, "", "1"), (True, "", "2")]
    client.edit_message_text.return_value = (False, "edit failed")
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)

    with caplog.at_level(logging.INFO, logger="gateway"):
        sink.finalize("fallback message")

    assert "outbound chat=123 text='fallback message'" in caplog.text


def test_finalize_renders_headers_and_tables_as_html() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.finalize("## Open PRs\n\n| # | Title |\n|---|---|\n| 3811 | docs fix |\n\n---\n\n**Done**")

    html_text = client.edit_message_text.call_args.args[2]
    assert "<b>Open PRs</b>" in html_text
    assert "• <b>3811</b> — docs fix" in html_text
    assert "<b>Done</b>" in html_text
    assert "|---|" not in html_text


def test_finalize_renders_markdown_as_html() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.finalize("**bold** and `code`")

    call = client.edit_message_text.call_args
    assert call.kwargs.get("parse_mode") == "HTML"
    assert "<b>bold</b>" in call.args[2]
    assert "<code>code</code>" in call.args[2]


def test_finalize_falls_back_to_plain_when_html_rejected() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    # HTML edit fails (bad markup); plain retry succeeds.
    client.edit_message_text.side_effect = [(False, "can't parse entities"), (True, "")]
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.finalize("**bold**")

    assert client.edit_message_text.call_count == 2
    first, second = client.edit_message_text.call_args_list
    assert first.kwargs.get("parse_mode") == "HTML"
    assert second.kwargs.get("parse_mode", "") == ""
    assert second.args[2] == "**bold**"


def test_render_error_appends_auth_login_hint_on_credit_exhaustion() -> None:
    from core.llm.shared.llm_retry import CREDIT_EXHAUSTED_MARKER

    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.render_error(f"Anthropic {CREDIT_EXHAUSTED_MARKER}. Original error: 400")

    finalized = client.edit_message_text.call_args[0][2]
    assert "opensre auth login" in finalized


def test_render_error_no_auth_hint_for_generic_error() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.render_error("something else broke")

    finalized = client.edit_message_text.call_args[0][2]
    assert "opensre auth login" not in finalized


def test_render_error_strips_raw_exception_detail() -> None:
    # Arrange
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)

    # Act: hand render_error a raw exception string with a secret in it.
    sink.render_error("RuntimeError: token sk-DO-NOT-LEAK rejected by db-host:5432")

    # Assert: the chat message carries none of the raw detail.
    finalized = client.edit_message_text.call_args[0][2]
    assert "sk-DO-NOT-LEAK" not in finalized
    assert "db-host" not in finalized
    assert "Something went wrong" in finalized


def test_goal_continuation_keeps_both_streamed_answers() -> None:
    """After finalize, a later outer-turn stream must not overwrite the first answer.

    ``run_until_session_goal`` reuses one sink. Editing the same Telegram
    message_id would replace step one with step two; continuation must send a
    new message so both turns remain visible.
    """
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.side_effect = [
        (True, "", "1"),  # initial placeholder
        (True, "", "2"),  # continuation placeholder / answer
    ]
    client.edit_message_text.return_value = (True, "")
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)

    first = sink.stream(label="assistant", chunks=["step one"])
    second = sink.stream(label="assistant", chunks=["step two"])

    assert first == "step one"
    assert second == "step two"
    # First answer edited into message 1; second turn must not keep editing 1.
    edited_ids = [call.args[1] for call in client.edit_message_text.call_args_list]
    assert "1" in edited_ids
    # Continuation either edits a new id or lands as a fresh send.
    assert "2" in edited_ids or client.send_message.call_count >= 2
    # The first answer text must still be what message 1 last received.
    edits_on_first = [
        call.args[2] for call in client.edit_message_text.call_args_list if call.args[1] == "1"
    ]
    assert any("step one" in text for text in edits_on_first)
    assert not any("step two" in text for text in edits_on_first)


def test_finalize_after_finished_turn_sends_a_new_message() -> None:
    """A finalize-only continuation must not overwrite the prior answer in place."""
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.side_effect = [
        (True, "", "1"),
        (True, "", "2"),
    ]
    client.edit_message_text.return_value = (True, "")
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.finalize("step one")
    sink.finalize("step two from finalize")

    edits_on_first = [
        call.args[2] for call in client.edit_message_text.call_args_list if call.args[1] == "1"
    ]
    assert any("step one" in text for text in edits_on_first)
    assert not any("step two from finalize" in text for text in edits_on_first)
    # Second answer arrives via a new send (or edit of a new id).
    sent_texts = [call.args[1] for call in client.send_message.call_args_list]
    assert any("step two from finalize" in text for text in sent_texts) or any(
        call.args[1] == "2" and "step two from finalize" in call.args[2]
        for call in client.edit_message_text.call_args_list
    )


def test_a_second_goal_turn_posts_a_new_message_instead_of_overwriting() -> None:
    """Session goal continuation runs several turns through one sink.

    The first answer replaces the placeholder by editing it. A later turn must
    not edit that same message — doing so overwrites the answer the user has
    already read, so a five-step goal ends up showing only step five.
    """
    # Arrange.
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = TelegramTurnOutput(client=client, chat_id="123", edit_interval_seconds=0.0)

    # Act: two turns of one continued goal.
    sink.finalize("turn one answer")
    client.send_message.reset_mock()
    sink.finalize("turn two answer")

    # Assert: the second turn was delivered as its own message.
    assert client.send_message.called, "second goal turn overwrote the first answer"
    posted = client.send_message.call_args.args[1]
    assert "turn two answer" in posted
