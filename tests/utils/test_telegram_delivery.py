"""Tests for app/utils/telegram_delivery.py."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.utils.telegram_delivery import (
    _TelegramTokenFilter,
    post_telegram_message,
    send_telegram_report,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code: int, body: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


# ---------------------------------------------------------------------------
# post_telegram_message
# ---------------------------------------------------------------------------


def test_post_telegram_message_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.telegram_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(200, {"ok": True, "result": {"message_id": 42}}),
    )
    ok, error, message_id = post_telegram_message("chat-1", "hello", "bot-token")
    assert ok is True
    assert error == ""
    assert message_id == "42"


def test_post_telegram_message_sends_correct_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["url"] = url
        captured["json"] = json
        return _mock_response(200, {"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr("app.utils.telegram_delivery.httpx.post", _fake_post)
    post_telegram_message("chat-42", "test text", "my-token")

    assert "my-token" in captured["url"]
    assert "sendMessage" in captured["url"]
    assert captured["json"]["chat_id"] == "chat-42"
    assert captured["json"]["text"] == "test text"


def test_post_telegram_message_with_reply_to(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["json"] = json
        return _mock_response(200, {"ok": True, "result": {"message_id": 2}})

    monkeypatch.setattr("app.utils.telegram_delivery.httpx.post", _fake_post)
    post_telegram_message("chat-1", "text", "token", reply_to_message_id="99")
    assert captured["json"]["reply_to_message_id"] == 99


def test_post_telegram_message_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.telegram_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(
            400, {"ok": False, "description": "Bad Request: chat not found"}
        ),
    )
    ok, error, message_id = post_telegram_message("chat-1", "text", "bot-token")
    assert ok is False
    assert "Bad Request" in error
    assert message_id == ""


def test_post_telegram_message_exception_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError("network down")

    monkeypatch.setattr("app.utils.telegram_delivery.httpx.post", _raise)
    ok, error, message_id = post_telegram_message("chat-1", "text", "bot-token")
    assert ok is False
    assert "network down" in error
    assert message_id == ""


def test_post_telegram_message_exception_redacts_token(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "secret-bot-token-123"

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError(f"failed to connect to api.telegram.org/bot{secret}/sendMessage")

    monkeypatch.setattr("app.utils.telegram_delivery.httpx.post", _raise)
    ok, error, _ = post_telegram_message("chat-1", "text", secret)
    assert ok is False
    assert secret not in error
    assert "<redacted>" in error


# ---------------------------------------------------------------------------
# send_telegram_report
# ---------------------------------------------------------------------------


def test_send_telegram_report_posts_to_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["url"] = url
        captured["json"] = json
        return _mock_response(200, {"ok": True, "result": {"message_id": 5}})

    monkeypatch.setattr("app.utils.telegram_delivery.httpx.post", _fake_post)
    ok, error = send_telegram_report("Report text", {"bot_token": "tok", "chat_id": "chat-1"})

    assert ok is True
    assert error == ""
    assert "tok" in captured["url"]
    assert captured["json"]["chat_id"] == "chat-1"
    assert captured["json"]["text"] == "Report text"


def test_send_telegram_report_uses_reply_to_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["json"] = json
        return _mock_response(200, {"ok": True, "result": {"message_id": 6}})

    monkeypatch.setattr("app.utils.telegram_delivery.httpx.post", _fake_post)
    send_telegram_report(
        "Report",
        {"bot_token": "tok", "chat_id": "chat-1", "reply_to_message_id": "77"},
    )
    assert captured["json"].get("reply_to_message_id") == 77


def test_send_telegram_report_returns_false_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.telegram_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(403, {"ok": False, "description": "Forbidden"}),
    )
    ok, error = send_telegram_report("Report", {"bot_token": "tok", "chat_id": "chat-1"})
    assert ok is False
    assert "Forbidden" in error


# ---------------------------------------------------------------------------
# fix 1 – httpx log token filter
# ---------------------------------------------------------------------------


def test_telegram_token_filter_scrubs_url_from_msg() -> None:
    f = _TelegramTokenFilter()
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="HTTP Request: POST https://api.telegram.org/botSECRET123/sendMessage",
        args=(),
        exc_info=None,
    )
    f.filter(record)
    assert "SECRET123" not in record.msg
    assert "bot<redacted>/sendMessage" in record.msg


def test_telegram_token_filter_scrubs_token_in_args() -> None:
    f = _TelegramTokenFilter()
    record = logging.LogRecord(
        name="httpcore",
        level=logging.DEBUG,
        pathname="",
        lineno=0,
        msg="send %s",
        args=("https://api.telegram.org/botSECRET123/sendMessage",),
        exc_info=None,
    )
    f.filter(record)
    assert isinstance(record.args, tuple)
    assert "SECRET123" not in record.args[0]
    assert "bot<redacted>/sendMessage" in record.args[0]


def test_telegram_token_filter_scrubs_non_string_url_arg() -> None:
    """httpx passes request.url as an httpx.URL object, not a plain str."""

    class FakeURL:
        def __str__(self) -> str:
            return "https://api.telegram.org/botSECRET123/sendMessage"

    f = _TelegramTokenFilter()
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='HTTP Request: %s %s "%s %d %s"',
        args=("POST", FakeURL(), "HTTP/1.1", 200, "OK"),
        exc_info=None,
    )
    f.filter(record)
    assert isinstance(record.args, tuple)
    assert "SECRET123" not in str(record.args[1])
    assert "bot<redacted>/sendMessage" in str(record.args[1])


# ---------------------------------------------------------------------------
# fix 2 – send_telegram_report guards missing creds
# ---------------------------------------------------------------------------


def test_send_telegram_report_missing_bot_token() -> None:
    ok, error = send_telegram_report("report", {"bot_token": "", "chat_id": "chat-1"})
    assert ok is False
    assert "Missing" in error


def test_send_telegram_report_missing_chat_id() -> None:
    ok, error = send_telegram_report("report", {"bot_token": "tok", "chat_id": ""})
    assert ok is False
    assert "Missing" in error


def test_send_telegram_report_missing_both_creds() -> None:
    ok, error = send_telegram_report("report", {})
    assert ok is False
    assert "Missing" in error


# ---------------------------------------------------------------------------
# fix 3 – resp.json() only called after status check (non-JSON error body)
# ---------------------------------------------------------------------------


def test_post_telegram_message_non_json_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = MagicMock()
    resp.status_code = 502
    resp.json.side_effect = ValueError("not JSON")
    resp.text = "Bad Gateway"

    monkeypatch.setattr("app.utils.telegram_delivery.httpx.post", lambda *_a, **_kw: resp)
    ok, error, message_id = post_telegram_message("chat-1", "text", "tok")

    assert ok is False
    assert "Bad Gateway" in error
    assert message_id == ""


# ---------------------------------------------------------------------------
# fix 4 – reply_to_message_id="0" must not be sent to the API
# ---------------------------------------------------------------------------


def test_post_telegram_message_reply_to_zero_string_not_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["json"] = json
        return _mock_response(200, {"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr("app.utils.telegram_delivery.httpx.post", _fake_post)
    post_telegram_message("chat-1", "text", "tok", reply_to_message_id="0")
    assert "reply_to_message_id" not in captured["json"]


def test_send_telegram_report_truncates_to_4096(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        "app.utils.telegram_delivery.httpx.post",
        lambda *_a, **kw: (
            captured.update({"text": kw["json"].get("text", "")})
            or _mock_response(200, {"ok": True, "result": {"message_id": 7}})
        ),  # type: ignore[misc]
    )
    long_report = "x" * 5000
    send_telegram_report(long_report, {"bot_token": "tok", "chat_id": "chat-1"})
    assert len(captured["text"]) == 4096
    assert captured["text"].endswith("…")
