"""Tests for utils/telegram_delivery.py."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any
from unittest.mock import MagicMock

import pytest

from integrations.telegram import delivery as telegram_delivery
from integrations.telegram.delivery import (
    _TelegramTokenFilter,
    post_telegram_message,
    send_telegram_report,
    truncate_for_telegram_html,
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
        "infrastructure.delivery.notifications.delivery_transport.httpx.post",
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

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _fake_post
    )
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

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _fake_post
    )
    post_telegram_message("chat-1", "text", "token", reply_to_message_id="99")
    assert captured["json"]["reply_to_message_id"] == 99


def test_post_telegram_message_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post",
        lambda *_a, **_kw: _mock_response(
            400, {"ok": False, "description": "Bad Request: chat not found"}
        ),
    )
    ok, error, message_id = post_telegram_message("chat-1", "text", "bot-token")
    assert ok is False
    assert "Bad Request" in error
    assert message_id == ""


def test_post_telegram_message_api_error_redacts_token_from_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "123456:SECRET-TOKEN"
    description = f"Unauthorized: https://api.telegram.org/bot{token}/sendMessage"

    def _fake_post(*_args: Any, **_kwargs: Any) -> MagicMock:
        return _mock_response(HTTPStatus.UNAUTHORIZED, {"ok": False, "description": description})

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _fake_post
    )

    ok, error, _ = post_telegram_message("chat-1", "text", token)

    assert ok is False
    assert token not in error
    assert "<redacted>" in error


def test_post_telegram_message_api_error_redacts_token_from_raw_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "123456:SECRET-TOKEN"
    body = f"upstream failure: https://api.telegram.org/bot{token}/sendMessage"

    def _fake_post(*_args: Any, **_kwargs: Any) -> MagicMock:
        response = _mock_response(HTTPStatus.INTERNAL_SERVER_ERROR, {})
        response.text = body
        return response

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _fake_post
    )

    ok, error, _ = post_telegram_message("chat-1", "text", token)

    assert ok is False
    assert token not in error
    assert "<redacted>" in error


def test_post_telegram_message_exception_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError("network down")

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _raise
    )
    ok, error, message_id = post_telegram_message("chat-1", "text", "bot-token")
    assert ok is False
    assert "network down" in error
    assert message_id == ""


def test_post_telegram_message_exception_redacts_token(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "secret-bot-token-123"

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError(f"failed to connect to api.telegram.org/bot{secret}/sendMessage")

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _raise
    )
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

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _fake_post
    )
    ok, error = send_telegram_report("Report text", {"bot_token": "tok", "chat_id": "chat-1"})

    assert ok is True
    assert error == ""
    assert "tok" in captured["url"]
    assert captured["json"]["chat_id"] == "chat-1"
    assert captured["json"]["text"] == "Report text"
    assert captured["json"]["parse_mode"] == "HTML"


def test_send_telegram_report_uses_reply_to_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["json"] = json
        return _mock_response(200, {"ok": True, "result": {"message_id": 6}})

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _fake_post
    )
    send_telegram_report(
        "Report",
        {"bot_token": "tok", "chat_id": "chat-1", "reply_to_message_id": "77"},
    )
    assert captured["json"].get("reply_to_message_id") == 77


def test_send_telegram_report_passes_reply_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["json"] = json
        return _mock_response(200, {"ok": True, "result": {"message_id": 7}})

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _fake_post
    )
    markup = {"inline_keyboard": [[{"text": "Open", "url": "https://x"}]]}
    ok, err = send_telegram_report(
        "Hi",
        {"bot_token": "tok", "chat_id": "c1"},
        reply_markup=markup,
    )
    assert ok is True
    assert err == ""
    assert captured["json"]["reply_markup"] == markup


def test_send_telegram_report_returns_false_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post",
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

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post",
        lambda *_a, **_kw: resp,
    )
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

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _fake_post
    )
    post_telegram_message("chat-1", "text", "tok", reply_to_message_id="0")
    assert "reply_to_message_id" not in captured["json"]


def test_send_telegram_report_truncates_to_4096(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post",
        lambda *_a, **kw: (
            captured.update({"text": kw["json"].get("text", "")})
            or _mock_response(200, {"ok": True, "result": {"message_id": 7}})
        ),  # type: ignore[misc]
    )
    long_report = "x" * 5000
    send_telegram_report(long_report, {"bot_token": "tok", "chat_id": "chat-1"})
    assert len(captured["text"]) == 4096
    assert captured["text"].endswith("…")


def test_truncate_for_telegram_html_strips_incomplete_trailing_tag() -> None:
    # Narrow limit so slice lands after ``<b>`` opens but well before ``</b>``.
    src = "<b>" + ("m" * 80)
    out = truncate_for_telegram_html(src, 18, suffix="…")
    assert len(out) <= 18
    assert out.endswith("…")
    assert not out[:-1].replace("…", "").endswith("<")


def test_truncate_for_telegram_html_balances_open_tags_within_limit() -> None:
    long_inner = "z" * 6000
    src = f"<b>start {long_inner}</b>"
    out = truncate_for_telegram_html(src, 4096, suffix="…")
    assert len(out) == 4096
    assert out.endswith("…")
    assert out.count("<b>") == out.count("</b>")


def test_truncate_for_telegram_html_closes_open_pre_block() -> None:
    # Fenced code blocks render as <pre>, so an evidence dump that straddles the
    # limit gets cut with <pre> still open and the Bot API rejects the message.
    src = "<pre>" + ("pool exhausted acquire timeout 30s\n" * 200) + "</pre>"
    out = truncate_for_telegram_html(src, 4096, suffix="…")
    assert len(out) <= 4096
    assert out.count("<pre>") == out.count("</pre>") == 1


def test_truncate_for_telegram_html_closes_open_strikethrough() -> None:
    src = "<s>" + ("superseded " * 500) + "</s>"
    out = truncate_for_telegram_html(src, 512, suffix="…")
    assert len(out) <= 512
    assert out.count("<s>") == out.count("</s>") == 1


def test_truncate_for_telegram_html_keeps_bare_angle_pair_as_text() -> None:
    # "<>" carries no element name: a SQL inequality predicate, not markup.
    src = "<b>slow query</b> SELECT id FROM jobs WHERE state <> 'done'; " * 100
    out = truncate_for_telegram_html(src, 4096, suffix="…")
    assert len(out) <= 4096
    assert "<>" in out
    assert out.count("<b>") == out.count("</b>")


def test_truncate_for_telegram_html_ignores_whitespace_only_tag() -> None:
    src = "< > spaced angle pair " * 400
    out = truncate_for_telegram_html(src, 256, suffix="…")
    assert len(out) <= 256
    assert out.endswith("…")


def test_truncate_for_telegram_html_noop_when_under_limit() -> None:
    s = "<i>ok</i>"
    assert truncate_for_telegram_html(s, 50) == s


# ---------------------------------------------------------------------------
# Shared-transport delegation (regression coverage for the #864 refactor)
# ---------------------------------------------------------------------------


class TestDelegatesToSharedTransport:
    """After #864 the telegram helper uses ``delivery_transport.post_json``
    rather than calling httpx directly. Pins the contract so re-importing
    httpx into ``telegram_delivery`` regresses loudly."""

    def test_module_does_not_import_httpx(self) -> None:
        # Reuse the module-level ``from integrations.telegram_delivery import delivery as telegram_delivery`` (renamed for cleanliness)
        # to avoid importing the same module via both ``import`` and
        # ``from import`` styles (CodeQL py/import-and-import-from).
        assert not hasattr(telegram_delivery, "httpx"), (
            "telegram_delivery should not import httpx directly — "
            "it must go through delivery_transport.post_json"
        )

    def test_post_message_uses_post_json_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from infrastructure.delivery.notifications.delivery_transport import DeliveryResponse

        captured: dict[str, Any] = {}

        def _stub_post_json(url: str, payload: dict, **kw: Any) -> DeliveryResponse:
            captured["url"] = url
            captured["payload"] = payload
            return DeliveryResponse(
                ok=True, status_code=200, data={"ok": True, "result": {"message_id": 42}}
            )

        monkeypatch.setattr("integrations.telegram.delivery.post_json", _stub_post_json)
        ok, err, mid = post_telegram_message("chat-1", "hello", "secret-bot-tok")
        assert ok is True
        assert err == ""
        assert mid == "42"
        assert "/bot" in captured["url"]
        assert captured["payload"]["chat_id"] == "chat-1"
        assert captured["payload"]["text"] == "hello"

    def test_transport_failure_redacts_token_in_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the shared helper returns ``ok=False`` because the transport
        raised, telegram must still scrub the bot token out of the error
        string before propagating it."""
        from infrastructure.delivery.notifications.delivery_transport import DeliveryResponse

        bot_token = "1234567890:ABCDEFverysecretvalue"
        leak_msg = f"connect failed for url=https://api.telegram.org/bot{bot_token}/sendMessage"

        monkeypatch.setattr(
            "integrations.telegram.delivery.post_json",
            lambda *_a, **_kw: DeliveryResponse(ok=False, error=leak_msg),
        )
        ok, err, _ = post_telegram_message("chat-1", "hi", bot_token)
        assert ok is False
        assert bot_token not in err
        assert "<redacted>" in err
