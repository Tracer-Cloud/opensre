from __future__ import annotations

from http import HTTPStatus
from types import MappingProxyType
from unittest.mock import MagicMock, patch

import pytest

from gateway.transports.telegram.poller.client import TelegramBotClient
from infrastructure.delivery.notifications.delivery_transport import DeliveryResponse


@patch("gateway.transports.telegram.poller.client.post_json")
def test_send_message_success(mock_post: MagicMock) -> None:
    mock_post.return_value = MagicMock(
        ok=True,
        status_code=200,
        data={"ok": True, "result": {"message_id": 99}},
    )
    client = TelegramBotClient("token")
    ok, error, message_id = client.send_message("123", "hello")
    assert ok is True
    assert error == ""
    assert message_id == "99"


@patch("gateway.transports.telegram.poller.client.post_json")
def test_send_message_success_with_mapping_proxy_data(mock_post: MagicMock) -> None:
    mock_post.return_value = DeliveryResponse(
        ok=True,
        status_code=200,
        data=MappingProxyType({"ok": True, "result": {"message_id": 42}}),
    )
    client = TelegramBotClient("token")
    ok, error, message_id = client.send_message("123", "hello")
    assert ok is True
    assert error == ""
    assert message_id == "42"


@patch("gateway.transports.telegram.poller.client.post_json")
def test_send_message_redacts_bot_token_from_transport_exception(
    mock_post: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    token = "123:SECRET"
    safe_error = "Connection refused for https://api.telegram.org/bot<redacted>/sendMessage"
    mock_post.return_value = DeliveryResponse(
        ok=False,
        error=f"Connection refused for https://api.telegram.org/bot{token}/sendMessage",
        exc_type="ConnectError",
    )
    caplog.set_level(logging.WARNING, logger="gateway.transports.telegram.poller.client")

    ok, error, message_id = TelegramBotClient(token).send_message("123", "hello")

    assert ok is False
    assert message_id == ""
    assert error == safe_error
    assert token not in error
    assert token not in caplog.text
    assert safe_error in caplog.text


@patch("gateway.transports.telegram.poller.client.post_json")
def test_send_message_redacts_bot_token_from_http_response_body(
    mock_post: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    token = "123:SECRET"
    safe_error = "https://api.telegram.org/bot<redacted>/sendMessage"
    mock_post.return_value = DeliveryResponse(
        ok=True,
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        text=f"upstream failure: https://api.telegram.org/bot{token}/sendMessage",
    )
    caplog.set_level(logging.WARNING, logger="gateway.transports.telegram.poller.client")

    ok, error, message_id = TelegramBotClient(token).send_message("123", "hello")

    assert ok is False
    assert message_id == ""
    assert error == f"upstream failure: {safe_error}"
    assert token not in caplog.text
    assert safe_error in caplog.text


@patch("gateway.transports.telegram.poller.client.post_json")
def test_send_message_redacts_bot_token_from_provider_description(
    mock_post: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    token = "123:SECRET"
    safe_error = "https://api.telegram.org/bot<redacted>/sendMessage"
    mock_post.return_value = DeliveryResponse(
        ok=True,
        status_code=HTTPStatus.OK,
        data={
            "ok": False,
            "description": f"Telegram rejected https://api.telegram.org/bot{token}/sendMessage",
        },
    )
    caplog.set_level(logging.WARNING, logger="gateway.transports.telegram.poller.client")

    ok, error, message_id = TelegramBotClient(token).send_message("123", "hello")

    assert ok is False
    assert message_id == ""
    assert error == f"Telegram rejected {safe_error}"
    assert token not in caplog.text
    assert safe_error in caplog.text
