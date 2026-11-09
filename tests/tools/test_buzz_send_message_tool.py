"""Tests for the Buzz message action surface."""

from integrations.buzz.tools.buzz_send_message_tool import (
    BuzzSendMessageTool,
    buzz_send_message,
)


def test_metadata_allows_external_send_without_approval() -> None:
    metadata = BuzzSendMessageTool.metadata()

    assert metadata.name == "buzz_send_message"
    assert metadata.source == "buzz"
    assert metadata.side_effect_level == "external"
    assert buzz_send_message.requires_approval is False
    assert buzz_send_message.__opensre_registered_tool__.surfaces == ("action",)
