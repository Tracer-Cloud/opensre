from __future__ import annotations

from http import HTTPStatus
from unittest.mock import MagicMock, patch

import httpx
import pytest

from gateway.transports.telegram.poller.poller import (
    TelegramPoller,
    TelegramPollResult,
    _decode_telegram_response,
)


def test_decode_telegram_response_parses_non_200_json() -> None:
    response = httpx.Response(
        HTTPStatus.CONFLICT,
        json={
            "ok": False,
            "error_code": HTTPStatus.CONFLICT,
            "description": "Conflict: terminated by other getUpdates request",
        },
    )
    data = _decode_telegram_response(response)
    assert data["ok"] is False
    assert data["error_code"] == 409


@patch("gateway.transports.telegram.poller.poller.time.sleep")
@patch("gateway.transports.telegram.poller.poller.httpx.get")
def test_poll_once_conflict_is_debug_not_warning(
    mock_get: MagicMock,
    mock_sleep: MagicMock,
    caplog: object,
) -> None:
    import logging

    caplog.set_level(logging.DEBUG, logger="gateway.transports.telegram.poller.poller")
    mock_get.return_value = httpx.Response(
        409,
        json={
            "ok": False,
            "error_code": 409,
            "description": "Conflict: terminated by other getUpdates request",
        },
    )
    poller = TelegramPoller("tok")
    assert poller.poll_once() == TelegramPollResult()
    mock_sleep.assert_called_once_with(2.0)
    assert not any(
        "[telegram-gateway] getUpdates not ok" in record.message for record in caplog.records
    )


@patch("gateway.transports.telegram.poller.poller.time.sleep")
@patch("gateway.transports.telegram.poller.poller.httpx.get")
def test_poll_once_success_resets_conflict_backoff(
    mock_get: MagicMock, _mock_sleep: MagicMock
) -> None:
    mock_get.side_effect = [
        httpx.Response(
            409,
            json={"ok": False, "error_code": 409, "description": "conflict"},
        ),
        httpx.Response(200, json={"ok": True, "result": []}),
    ]
    poller = TelegramPoller("tok")
    poller._conflict_backoff_seconds = 8.0
    assert poller.poll_once() == TelegramPollResult()
    assert poller.poll_once() == TelegramPollResult()
    assert poller._conflict_backoff_seconds == 2.0


@patch("gateway.transports.telegram.poller.poller.time.sleep")
@patch("gateway.transports.telegram.poller.poller.httpx.get")
def test_poll_once_parses_inbound_message(mock_get: MagicMock, mock_sleep: MagicMock) -> None:
    mock_get.return_value = httpx.Response(
        200,
        json={
            "ok": True,
            "result": [
                {
                    "update_id": 7,
                    "message": {
                        "message_id": 11,
                        "from": {"id": 42},
                        "chat": {"id": 99, "type": "private"},
                        "text": "hello",
                    },
                }
            ],
        },
    )
    batch = TelegramPoller("tok").poll_once()
    assert len(batch.messages) == 1
    assert batch.messages[0].text == "hello"
    assert batch.messages[0].chat_id == "99"
    assert batch.callbacks == []
    mock_sleep.assert_not_called()


@patch("gateway.transports.telegram.poller.poller.time.sleep")
@patch("gateway.transports.telegram.poller.poller.httpx.get")
def test_poll_once_parses_callback_query(mock_get: MagicMock, mock_sleep: MagicMock) -> None:
    mock_get.return_value = httpx.Response(
        200,
        json={
            "ok": True,
            "result": [
                {
                    "update_id": 8,
                    "callback_query": {
                        "id": "cq-9",
                        "from": {"id": 42},
                        "data": "opensre_approval_approve:abc",
                        "message": {
                            "message_id": 3,
                            "chat": {"id": 99, "type": "private"},
                        },
                    },
                }
            ],
        },
    )
    batch = TelegramPoller("tok").poll_once()
    assert batch.messages == []
    assert len(batch.callbacks) == 1
    assert batch.callbacks[0].data == "opensre_approval_approve:abc"
    assert batch.callbacks[0].chat_id == "99"
    # Ensure getUpdates asked for callback_query updates.
    assert "callback_query" in mock_get.call_args.kwargs["params"]["allowed_updates"]
    mock_sleep.assert_not_called()


@patch("gateway.transports.telegram.poller.poller.time.sleep")
@patch("gateway.transports.telegram.poller.poller.httpx.get")
def test_poll_once_redacts_bot_token_from_transport_exception(
    mock_get: MagicMock,
    mock_sleep: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    token = "123:SECRET"
    mock_get.side_effect = httpx.ConnectError(
        f"Connection refused for https://api.telegram.org/bot{token}/getUpdates"
    )
    caplog.set_level(logging.DEBUG, logger="gateway.transports.telegram.poller.poller")

    result = TelegramPoller(token).poll_once()

    assert result == TelegramPollResult()
    mock_sleep.assert_called_once_with(2.0)
    assert token not in caplog.text
    assert "https://api.telegram.org/bot<redacted>/getUpdates" in caplog.text
    assert "ConnectError" in caplog.text
