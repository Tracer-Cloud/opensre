"""Tests for app/utils/discord_delivery.py."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.utils.discord_delivery import (
    _DiscordTokenFilter,
    create_discord_thread,
    post_discord_message,
    send_discord_report,
)


# ---------------------------------------------------------------------------
# fix 1 – httpx log token filter
# ---------------------------------------------------------------------------


def test_discord_token_filter_scrubs_msg() -> None:
    f = _DiscordTokenFilter()
    token = "DISCORD_TOKEN_PART_1_XXX.ABCDEF.DISCORD_TOKEN_PART_3_XXXXXXXX"
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=f"HTTP Request: POST https://discord.com/api/v10/... Authorization: Bot {token}",
        args=(),
        exc_info=None,
    )
    f.filter(record)
    assert token not in record.msg
    assert "<redacted>" in record.msg


def test_discord_token_filter_scrubs_args() -> None:
    f = _DiscordTokenFilter()
    token = "DISCORD_TOKEN_PART_1_XXX.ABCDEF.DISCORD_TOKEN_PART_3_XXXXXXXX"
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Authorization: %s",
        args=(f"Bot {token}",),
        exc_info=None,
    )
    f.filter(record)
    assert isinstance(record.args, tuple)
    assert token not in record.args[0]
    assert "<redacted>" in record.args[0]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code: int, body: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def _mock_non_json_response(status_code: int, text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.side_effect = ValueError("not JSON")
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# post_discord_message
# ---------------------------------------------------------------------------


def test_post_discord_message_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(200, {"id": "msg-123"}),
    )
    ok, error, message_id = post_discord_message("chan-1", [{"title": "Alert"}], "bot-token")
    assert ok is True
    assert error == ""
    assert message_id == "msg-123"


def test_post_discord_message_201_also_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(201, {"id": "msg-456"}),
    )
    ok, _, message_id = post_discord_message("chan-1", [], "bot-token")
    assert ok is True
    assert message_id == "msg-456"


def test_post_discord_message_sends_correct_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(
        url: str, *, json: dict[str, Any], headers: dict[str, str], **_kw: Any
    ) -> MagicMock:
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _mock_response(200, {"id": "x"})

    monkeypatch.setattr("app.utils.discord_delivery.httpx.post", _fake_post)
    embeds = [{"title": "Test"}]
    post_discord_message("chan-42", embeds, "my-token", content="hello")

    assert "chan-42" in captured["url"]
    assert captured["json"]["content"] == "hello"
    assert captured["json"]["embeds"] == embeds
    assert captured["headers"]["Authorization"] == "Bot my-token"


def test_post_discord_message_failure_returns_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(403, {"message": "Missing Permissions"}),
    )
    ok, error, message_id = post_discord_message("chan-1", [], "bot-token")
    assert ok is False
    assert "Missing Permissions" in error
    assert message_id == ""


def test_post_discord_message_failure_falls_back_to_error_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(400, {"error": "Bad Request"}),
    )
    ok, error, _ = post_discord_message("chan-1", [], "bot-token")
    assert ok is False
    assert "Bad Request" in error


def test_post_discord_message_exception_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError("network down")

    monkeypatch.setattr("app.utils.discord_delivery.httpx.post", _raise)
    ok, error, message_id = post_discord_message("chan-1", [], "bot-token")
    assert ok is False
    assert "network down" in error
    assert message_id == ""


def test_post_discord_message_non_json_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.httpx.post",
        lambda *_a, **_kw: _mock_non_json_response(502, "<html><body>Bad Gateway</body></html>"),
    )
    ok, error, message_id = post_discord_message("chan-1", [], "bot-token")
    assert ok is False
    assert "Bad Gateway" in error
    assert message_id == ""


def test_post_discord_message_exception_redacts_token(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "DISCORD_TOKEN_PART_1_XXX.ABCDEF.DISCORD_TOKEN_PART_3_XXXXXXXX"

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError(f"failed with token {secret}")

    monkeypatch.setattr("app.utils.discord_delivery.httpx.post", _raise)
    caplog.set_level(logging.WARNING, logger="app.utils.discord_delivery")

    ok, error, _ = post_discord_message("chan-1", [], secret)

    assert ok is False
    assert secret not in error
    assert "<redacted>" in error
    assert secret not in caplog.text
    assert "<redacted>" in caplog.text


# ---------------------------------------------------------------------------
# create_discord_thread
# ---------------------------------------------------------------------------


def test_create_discord_thread_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(201, {"id": "thread-99"}),
    )
    ok, error, thread_id = create_discord_thread("chan-1", "msg-1", "My Thread", "bot-token")
    assert ok is True
    assert error == ""
    assert thread_id == "thread-99"


def test_create_discord_thread_sends_correct_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def _fake_post(url: str, **_kw: Any) -> MagicMock:
        captured["url"] = url
        return _mock_response(200, {"id": "t-1"})

    monkeypatch.setattr("app.utils.discord_delivery.httpx.post", _fake_post)
    create_discord_thread("chan-5", "msg-5", "Thread Name", "bot-token")
    assert "chan-5" in captured["url"]
    assert "msg-5" in captured["url"]
    assert "threads" in captured["url"]


def test_create_discord_thread_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(403, {"message": "Forbidden"}),
    )
    ok, error, thread_id = create_discord_thread("chan-1", "msg-1", "name", "bot-token")
    assert ok is False
    assert "Forbidden" in error
    assert thread_id == ""


def test_create_discord_thread_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setattr("app.utils.discord_delivery.httpx.post", _raise)
    ok, error, thread_id = create_discord_thread("chan-1", "msg-1", "name", "bot-token")
    assert ok is False
    assert "timed out" in error
    assert thread_id == ""


def test_create_discord_thread_non_json_plain_text_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.httpx.post",
        lambda *_a, **_kw: _mock_non_json_response(500, "plain text error"),
    )
    ok, error, thread_id = create_discord_thread("chan-1", "msg-1", "name", "bot-token")
    assert ok is False
    assert "plain text error" in error
    assert thread_id == ""


# ---------------------------------------------------------------------------
# send_discord_report
# ---------------------------------------------------------------------------


def test_send_discord_report_posts_to_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["url"] = url
        captured["embeds"] = json.get("embeds", [])
        return _mock_response(200, {"id": "m-1"})

    monkeypatch.setattr("app.utils.discord_delivery.httpx.post", _fake_post)
    ok, error = send_discord_report("Report text", {"channel_id": "chan-1", "bot_token": "tok"})

    assert ok is True
    assert error == ""
    assert "chan-1" in captured["url"]
    embed = captured["embeds"][0]
    assert embed["description"] == "Report text"
    assert embed["title"] == "Investigation Complete"
    assert embed["color"] == 15158332
    assert embed["footer"]["text"] == "OpenSRE Investigation"


def test_send_discord_report_prefers_thread_over_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, **_kw: Any) -> MagicMock:
        captured["url"] = url
        return _mock_response(200, {"id": "m-1"})

    monkeypatch.setattr("app.utils.discord_delivery.httpx.post", _fake_post)
    send_discord_report(
        "Report",
        {"channel_id": "chan-1", "thread_id": "thread-99", "bot_token": "tok"},
    )
    assert "thread-99" in captured["url"]
    assert "chan-1" not in captured["url"]


def test_send_discord_report_returns_false_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(403, {"message": "Forbidden"}),
    )
    ok, error = send_discord_report("Report", {"channel_id": "chan-1", "bot_token": "tok"})
    assert ok is False
    assert "Forbidden" in error


def test_send_discord_report_truncates_description_to_4096(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        "app.utils.discord_delivery.httpx.post",
        lambda *_a, **kw: (
            captured.update({"embeds": kw["json"].get("embeds", [])})
            or _mock_response(200, {"id": "m-1"})
        ),  # type: ignore[misc]
    )
    long_report = "x" * 5000
    send_discord_report(long_report, {"channel_id": "chan-1", "bot_token": "tok"})
    description = captured["embeds"][0]["description"]
    assert len(description) == 4096
    assert description.endswith("…")
