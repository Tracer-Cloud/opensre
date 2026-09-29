from __future__ import annotations

from typing import Any

import pytest

from gateway.transports.slack.processing.events import SlackInboundFile, parse_events_api_payload


def _mention_payload(**event_overrides: Any) -> dict[str, Any]:
    event = {
        "type": "app_mention",
        "user": "U111",
        "channel": "C222",
        "ts": "1700000000.000100",
        "text": "<@UBOT> check the checkout service",
    }
    event.update(event_overrides)
    return {"team_id": "T333", "event": event}


def test_parses_app_mention_and_strips_leading_bot_mention() -> None:
    inbound = parse_events_api_payload(_mention_payload())

    assert inbound is not None
    assert inbound.team_id == "T333"
    assert inbound.user_id == "U111"
    assert inbound.channel_id == "C222"
    assert inbound.text == "check the checkout service"
    assert inbound.thread_ts == "1700000000.000100"


def test_mention_inside_existing_thread_keeps_parent_thread_ts() -> None:
    inbound = parse_events_api_payload(_mention_payload(thread_ts="1699999999.000001"))

    assert inbound is not None
    assert inbound.thread_ts == "1699999999.000001"
    assert inbound.ts == "1700000000.000100"


def test_conversation_key_combines_team_channel_and_thread() -> None:
    inbound = parse_events_api_payload(_mention_payload())

    assert inbound is not None
    assert inbound.conversation_key == "T333:C222:1700000000.000100"


def test_identical_conversations_in_different_teams_stay_isolated() -> None:
    team_a = parse_events_api_payload(_mention_payload())
    payload_b = _mention_payload()
    payload_b["team_id"] = "T999"
    team_b = parse_events_api_payload(payload_b)

    assert team_a is not None and team_b is not None
    # Same channel and thread ids, different workspaces: separate sessions/memory.
    assert team_a.conversation_key != team_b.conversation_key


def test_parses_direct_message() -> None:
    inbound = parse_events_api_payload(
        {
            "team_id": "T333",
            "event": {
                "type": "message",
                "channel_type": "im",
                "user": "U111",
                "channel": "D444",
                "ts": "1700000000.000200",
                "text": "what integrations do I have?",
            },
        }
    )

    assert inbound is not None
    assert inbound.channel_id == "D444"
    assert inbound.text == "what integrations do I have?"


def test_rejects_bot_echo_and_message_subtypes() -> None:
    assert parse_events_api_payload(_mention_payload(bot_id="B555")) is None
    assert parse_events_api_payload(_mention_payload(subtype="message_changed")) is None


_OPEN = {"open_channel_ids": {"C222"}}


def test_accepts_bot_mention_in_open_channel() -> None:
    inbound = parse_events_api_payload(
        _mention_payload(bot_id="B555", thread_ts="1699999999.000001"), **_OPEN
    )

    assert inbound is not None
    assert inbound.open_channel is True
    assert inbound.bot_id == "B555"
    assert inbound.user_id == "U111"
    assert inbound.addressed is True
    assert inbound.text == "check the checkout service"
    assert inbound.thread_ts == "1699999999.000001"


def test_bot_mention_without_user_uses_bot_id_as_speaker() -> None:
    payload = _mention_payload(bot_id="B555", subtype="bot_message")
    del payload["event"]["user"]

    inbound = parse_events_api_payload(payload, **_OPEN)

    assert inbound is not None
    assert inbound.user_id == "B555"


@pytest.mark.parametrize(
    "overrides",
    [
        {"bot_id": "B555", "channel": "C999"},  # channel not open
        {"bot_id": "B555", "type": "message", "channel_type": "channel"},  # not a mention
        {"bot_id": "B555", "subtype": "message_changed"},  # edit bookkeeping
    ],
)
def test_rejects_bot_events_outside_open_channel_mentions(overrides: dict[str, Any]) -> None:
    assert parse_events_api_payload(_mention_payload(**overrides), **_OPEN) is None


def test_bot_mentions_are_ignored_without_open_channels() -> None:
    assert parse_events_api_payload(_mention_payload(bot_id="B555")) is None


def test_human_mention_in_open_channel_is_flagged_open() -> None:
    inbound = parse_events_api_payload(_mention_payload(), **_OPEN)
    elsewhere = parse_events_api_payload(_mention_payload(channel="C999"), **_OPEN)

    assert inbound is not None and inbound.open_channel is True
    assert inbound.bot_id == ""
    assert elsewhere is not None and elsewhere.open_channel is False


def test_accepts_file_share_and_thread_broadcast_subtypes() -> None:
    # These subtypes still carry a real user mention and must be answered,
    # not silently dropped like edit/join bookkeeping subtypes.
    for subtype in ("file_share", "thread_broadcast"):
        inbound = parse_events_api_payload(_mention_payload(subtype=subtype))
        assert inbound is not None, f"{subtype} mention was dropped"
        assert inbound.text == "check the checkout service"


def test_rejects_channel_messages_without_mention() -> None:
    payload = _mention_payload(type="message", channel_type="channel")
    assert parse_events_api_payload(payload) is None


def test_rejects_payloads_missing_required_fields() -> None:
    assert parse_events_api_payload({}) is None
    assert parse_events_api_payload(_mention_payload(text="")) is None
    assert parse_events_api_payload(_mention_payload(user="")) is None
    assert parse_events_api_payload(_mention_payload(text="<@UBOT>")) is None


def test_untagged_thread_reply_parses_but_not_addressed() -> None:
    payload = {
        "team_id": "T333",
        "event": {
            "type": "message",
            "user": "U1",
            "channel": "C2",
            "ts": "200.2",
            "thread_ts": "100.1",
            "text": "and the second one?",
        },
    }
    inbound = parse_events_api_payload(payload)
    assert inbound is not None
    assert inbound.addressed is False
    assert inbound.thread_ts == "100.1"


def test_untagged_top_level_channel_message_is_dropped() -> None:
    payload = {
        "team_id": "T333",
        "event": {
            "type": "message",
            "user": "U1",
            "channel": "C2",
            "ts": "200.2",
            "text": "chatter",
        },
    }
    assert parse_events_api_payload(payload) is None


def test_app_mention_is_addressed() -> None:
    inbound = parse_events_api_payload(_mention_payload())
    assert inbound is not None
    assert inbound.addressed is True


def test_untagged_reply_keeps_leading_human_mention() -> None:
    """The attention gate must see '<@U2> …' to know the reply targets a human;
    only addressed messages get their leading (bot) mention stripped."""
    payload = {
        "team_id": "T333",
        "event": {
            "type": "message",
            "user": "U1",
            "channel": "C2",
            "ts": "200.2",
            "thread_ts": "100.1",
            "text": "<@U2> can you check the dashboard?",
        },
    }
    inbound = parse_events_api_payload(payload)
    assert inbound is not None
    assert inbound.text == "<@U2> can you check the dashboard?"


def _file(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": "F1",
        "name": "checkout.log",
        "mimetype": "text/plain",
        "size": 1024,
        "url_private": "https://files.slack.com/files-pri/T333-F1/checkout.log",
    }
    payload.update(overrides)
    return payload


def _file_share_payload(files: list[Any], text: str = "") -> dict[str, Any]:
    return {
        "team_id": "T333",
        "event": {
            "type": "message",
            "subtype": "file_share",
            "channel_type": "im",
            "user": "U111",
            "channel": "D222",
            "ts": "1700000000.000200",
            "text": text,
            "files": files,
        },
    }


def test_file_share_with_caption_carries_files_and_text() -> None:
    inbound = parse_events_api_payload(_file_share_payload([_file()], text="see attached"))

    assert inbound is not None
    assert inbound.text == "see attached"
    assert len(inbound.files) == 1
    assert inbound.files[0].id == "F1"
    assert inbound.files[0].name == "checkout.log"
    assert inbound.files[0].mimetype == "text/plain"
    assert inbound.files[0].url_private.endswith("checkout.log")


def test_file_only_message_with_no_caption_is_not_dropped() -> None:
    # Regression: a file shared with no caption must still produce a message,
    # instead of being dropped by the text-required guard.
    inbound = parse_events_api_payload(_file_share_payload([_file()], text=""))

    assert inbound is not None
    assert inbound.text == ""
    assert len(inbound.files) == 1


def test_message_with_neither_text_nor_files_is_ignored() -> None:
    assert parse_events_api_payload(_file_share_payload([], text="")) is None


def test_malformed_file_entries_are_skipped() -> None:
    files = [
        {"id": "F1", "url_private": "https://files.slack.com/x/F1"},  # minimal valid
        {"name": "no-id-or-url"},  # missing id + url → skipped
        "not-a-dict",  # skipped
    ]
    inbound = parse_events_api_payload(_file_share_payload(files, text="hi"))

    assert inbound is not None
    assert [file.id for file in inbound.files] == ["F1"]


def _single_file(**overrides: Any) -> SlackInboundFile:
    """Parse one file dict (with overrides) through the public payload path."""
    inbound = parse_events_api_payload(_file_share_payload([_file(**overrides)], text="hi"))
    assert inbound is not None
    assert len(inbound.files) == 1
    return inbound.files[0]


def test_file_name_falls_back_to_title_then_id() -> None:
    # Arrange/Act: a file with no name but a title.
    from_title = _single_file(name=None, title="Checkout Log")
    # Act: a file with neither name nor title.
    from_id = _single_file(name=None, title=None)

    # Assert: title fills in for a missing name; id is the last resort.
    assert from_title.name == "Checkout Log"
    assert from_id.name == "F1"


def test_file_mimetype_defaults_to_octet_stream_when_missing() -> None:
    assert _single_file(mimetype=None).mimetype == "application/octet-stream"


def test_file_url_private_falls_back_to_download_url() -> None:
    # Arrange/Act: no url_private, but a url_private_download is present.
    file = _single_file(url_private=None, url_private_download="https://files.slack.com/dl/F1")

    # Assert: the download URL is used.
    assert file.url_private == "https://files.slack.com/dl/F1"


def test_file_blank_id_is_skipped() -> None:
    # Arrange/Act: a whitespace-only id is not a usable id.
    inbound = parse_events_api_payload(_file_share_payload([_file(id="   ")], text="hi"))

    # Assert: the file is dropped (text keeps the message alive).
    assert inbound is not None
    assert inbound.files == ()


@pytest.mark.parametrize(
    ("raw_size", "expected"),
    [
        (2048, 2048),
        ("2048", 2048),  # Slack sometimes sends numeric strings
        (None, 0),  # missing → 0
        ("not-a-number", 0),  # invalid → 0
        (-5, 0),  # negative → clamped to 0
    ],
)
def test_file_size_is_coerced_to_non_negative_int(raw_size: Any, expected: int) -> None:
    assert _single_file(size=raw_size).size == expected
