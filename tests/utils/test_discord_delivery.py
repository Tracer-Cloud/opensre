from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.utils import discord_delivery
from app.utils.delivery_transport import DeliveryResponse
from app.utils.discord_delivery import (
    create_discord_thread,
    post_discord_message,
    send_discord_report,
)


def _mock_response(status_code: int, json_body: Any = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if isinstance(json_body, Exception):

        def _raise() -> Any:
            raise json_body

        resp.json.side_effect = _raise
    else:
        resp.json.return_value = json_body if json_body is not None else {}
    return resp


# ---------------------------------------------------------------------------
# post_discord_message
# ---------------------------------------------------------------------------


def test_post_discord_message_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.post_json",
        lambda *_, **__: DeliveryResponse(ok=True, status_code=200, data={"id": "msg-123"}),
    )
    ok, error, message_id = post_discord_message("chan-1", [], "bot-token")
    assert ok is True
    assert error == ""
    assert message_id == "msg-123"


def test_post_discord_message_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.post_json",
        lambda *_, **__: DeliveryResponse(
            ok=True,
            status_code=403,
            data={"message": "Missing Permissions"},
            text='{"message": "Missing Permissions"}',
        ),
    )
    ok, error, message_id = post_discord_message("chan-1", [], "bot-token")
    assert ok is False
    assert "Missing Permissions" in error
    assert message_id == ""


# ---------------------------------------------------------------------------
# Token Redaction & Hardening
# ---------------------------------------------------------------------------


def test_discord_token_filter_scrubs_msg() -> None:
    from app.utils.discord_delivery import _DiscordTokenFilter

    f = _DiscordTokenFilter()
    token = "DISCORD_TOKEN_PART_1_XXX.ABCDEF.DISCORD_TOKEN_PART_3_XXXXXXXX"
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=f"Request with token {token}",
        args=None,
        exc_info=None,
    )
    f.filter(record)
    assert token not in str(record.msg)
    assert "<redacted>" in str(record.msg)


def test_post_discord_message_redacts_token_in_error(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "MTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0.ABCDEF.MTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0NTY3"
    monkeypatch.setattr(
        "app.utils.discord_delivery.post_json",
        lambda *_, **__: DeliveryResponse(ok=False, error=f"Failed with {token}"),
    )
    ok, err, _ = post_discord_message("chan-1", [], token)
    assert ok is False
    assert token not in err
    assert "<redacted>" in err


def test_post_discord_message_handles_non_json_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.post_json",
        lambda *_, **__: DeliveryResponse(
            ok=True, status_code=502, data={}, text="<html>Bad Gateway</html>"
        ),
    )
    ok, err, _ = post_discord_message("chan-1", [], "tok")
    assert ok is False
    assert "unknown" in err


# ---------------------------------------------------------------------------
# create_discord_thread
# ---------------------------------------------------------------------------


def test_create_discord_thread_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.post_json",
        lambda *_, **__: DeliveryResponse(ok=True, status_code=201, data={"id": "thread-99"}),
    )
    ok, error, thread_id = create_discord_thread("chan-1", "msg-1", "My Thread", "bot-token")
    assert ok is True
    assert error == ""
    assert thread_id == "thread-99"


# ---------------------------------------------------------------------------
# send_discord_report
# ---------------------------------------------------------------------------


def test_send_discord_report_posts_to_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _stub_post_json(url: str, payload: dict, **kw: Any) -> DeliveryResponse:
        captured["url"] = url
        captured["payload"] = payload
        return DeliveryResponse(ok=True, status_code=200, data={"id": "m-1"})

    monkeypatch.setattr("app.utils.discord_delivery.post_json", _stub_post_json)
    ok, error = send_discord_report("Report text", {"channel_id": "chan-1", "bot_token": "tok"})

    assert ok is True
    assert error == ""
    assert "chan-1" in captured["url"]
    assert captured["payload"]["embeds"][0]["description"] == "Report text"


class TestDelegatesToSharedTransport:
    def test_post_message_uses_post_json_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, Any]] = []

        def _stub_post_json(url: str, payload: dict, **kw: Any) -> DeliveryResponse:
            calls.append({"url": url, "payload": payload, **kw})
            return DeliveryResponse(ok=True, status_code=200, data={"id": "m-via-helper"})

        monkeypatch.setattr("app.utils.discord_delivery.post_json", _stub_post_json)
        ok, _err, mid = post_discord_message("c1", [], "tok", content="hi")
        assert ok is True
        assert mid == "m-via-helper"
        assert "/channels/c1/messages" in calls[0]["url"]


def test_post_discord_message_201_also_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.post_json",
        lambda *_, **__: DeliveryResponse(ok=True, status_code=201, data={"id": "msg-201"}),
    )
    ok, error, message_id = post_discord_message("chan-1", [], "bot-token")
    assert ok is True
    assert message_id == "msg-201"


def test_send_discord_report_truncates_description_to_4096(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(*_, **kw):
        captured.update(kw)
        return DeliveryResponse(ok=True, status_code=200, data={"id": "x"})

    monkeypatch.setattr("app.utils.discord_delivery.post_json", _fake_post)
    long_text = "a" * 5000
    send_discord_report(long_text, {"channel_id": "c1", "bot_token": "tok"})
    # The payload is in 'payload' in my current mock structure, but user request says 'json'
    # Wait, my post_discord_message calls post_json(payload=...).
    # DeliveryResponse in send_discord_report uses post_discord_message.
    # Let's check how captured is updated.
    embeds = captured.get("payload", {}).get("embeds", [{}])
    assert len(embeds[0]["description"]) <= 4096


def test_send_discord_report_returns_false_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.post_json",
        lambda *_, **__: DeliveryResponse(ok=False, error="API down"),
    )
    ok, _ = send_discord_report("hi", {"channel_id": "c1", "bot_token": "tok"})
    assert ok is False


def test_create_discord_thread_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.discord_delivery.post_json",
        lambda *_, **__: DeliveryResponse(ok=False, error="forbidden"),
    )
    ok, error, thread_id = create_discord_thread("chan-1", "msg-1", "My Thread", "bot-token")
    assert ok is False
    assert thread_id == ""


def test_send_discord_report_prefers_thread_over_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _stub(url: str, payload: dict, **kw: Any) -> DeliveryResponse:
        captured["url"] = url
        return DeliveryResponse(ok=True, status_code=200, data={"id": "m-1"})

    monkeypatch.setattr("app.utils.discord_delivery.post_json", _stub)
    send_discord_report(
        "hi", {"channel_id": "chan-1", "thread_id": "thread-99", "bot_token": "tok"}
    )
    assert "thread-99" in captured["url"]
    assert "chan-1" not in captured["url"]
