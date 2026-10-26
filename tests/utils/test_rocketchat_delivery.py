"""Tests for integrations/rocketchat/delivery.py."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from infrastructure.delivery.notifications.delivery_transport import DeliveryResponse
from integrations.rocketchat import delivery as rocketchat_delivery
from integrations.rocketchat.delivery import (
    post_rocketchat_message,
    post_rocketchat_webhook,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SERVER = "https://chat.example.com"


def _mock_response(status_code: int, body: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def _ok_body(message_id: str = "msg-123") -> dict[str, Any]:
    return {"success": True, "message": {"_id": message_id}}


# ---------------------------------------------------------------------------
# post_rocketchat_message
# ---------------------------------------------------------------------------


def test_post_message_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post",
        lambda *_a, **_kw: _mock_response(200, _ok_body()),
    )
    ok, error, message_id = post_rocketchat_message(_SERVER, "#incidents", "hello", "tok", "u1")
    assert ok is True
    assert error == ""
    assert message_id == "msg-123"


def test_post_message_sends_correct_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(
        url: str, *, json: dict[str, Any], headers: dict[str, str], **_kw: Any
    ) -> MagicMock:
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _mock_response(200, _ok_body())

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _fake_post
    )
    attachments = [{"title": "Test"}]
    post_rocketchat_message(
        f"{_SERVER}/", "#incidents", "hello", "tok", "u1", attachments=attachments
    )

    assert captured["url"] == f"{_SERVER}/api/v1/chat.postMessage"
    assert captured["json"]["channel"] == "#incidents"
    assert captured["json"]["text"] == "hello"
    assert captured["json"]["attachments"] == attachments
    assert captured["headers"] == {"X-Auth-Token": "tok", "X-User-Id": "u1"}


def test_post_message_omits_attachments_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["json"] = json
        return _mock_response(200, _ok_body())

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _fake_post
    )
    post_rocketchat_message(_SERVER, "#incidents", "hello", "tok", "u1")
    assert "attachments" not in captured["json"]


def test_post_message_failure_returns_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post",
        lambda *_a, **_kw: _mock_response(400, {"success": False, "error": "error-room-not-found"}),
    )
    ok, error, message_id = post_rocketchat_message(_SERVER, "#nope", "hello", "tok", "u1")
    assert ok is False
    assert "error-room-not-found" in error
    assert message_id == ""


def test_post_message_http_200_but_success_false_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post",
        lambda *_a, **_kw: _mock_response(200, {"success": False, "error": "invalid-channel"}),
    )
    ok, error, _ = post_rocketchat_message(_SERVER, "#incidents", "hello", "tok", "u1")
    assert ok is False
    assert "invalid-channel" in error


def test_post_message_exception_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError("network down")

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _raise
    )
    ok, error, message_id = post_rocketchat_message(_SERVER, "#incidents", "hello", "tok", "u1")
    assert ok is False
    assert "network down" in error
    assert message_id == ""


def test_post_message_handles_html_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.rocketchat.delivery.post_json",
        lambda *_a, **_kw: DeliveryResponse(
            ok=True,
            status_code=502,
            data={},
            text="<html>Bad Gateway</html>",
        ),
    )
    ok, error, message_id = post_rocketchat_message(_SERVER, "#incidents", "hi", "tok", "u1")
    assert ok is False
    assert "<html>Bad Gateway</html>" in error
    assert message_id == ""


# ---------------------------------------------------------------------------
# Shared-transport delegation
# ---------------------------------------------------------------------------


class TestDelegatesToSharedTransport:
    """The Rocket.Chat helper must go through ``delivery_transport.post_json``
    rather than calling httpx directly, matching the other messaging vendors."""

    def test_module_does_not_import_httpx(self) -> None:
        assert not hasattr(rocketchat_delivery, "httpx"), (
            "rocketchat delivery should not import httpx directly — "
            "it must go through delivery_transport.post_json"
        )

    def test_post_message_uses_post_json_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, Any]] = []

        def _stub_post_json(url: str, payload: dict, **kw: Any) -> DeliveryResponse:
            calls.append({"url": url, "payload": payload, **kw})
            return DeliveryResponse(ok=True, status_code=200, data=_ok_body("m-via-helper"))

        monkeypatch.setattr("integrations.rocketchat.delivery.post_json", _stub_post_json)
        ok, _err, mid = post_rocketchat_message(_SERVER, "#c1", "hi", "tok", "u1")
        assert ok is True
        assert mid == "m-via-helper"
        assert calls and calls[0]["url"].endswith("/api/v1/chat.postMessage")
        assert calls[0]["headers"] == {"X-Auth-Token": "tok", "X-User-Id": "u1"}


# ---------------------------------------------------------------------------
# Token redaction
# ---------------------------------------------------------------------------


class TestRocketChatExceptionRedaction:
    def test_exception_error_redacts_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        token = "rc-secret-token-abc123"
        leak_msg = f"connect failed with {token}"

        monkeypatch.setattr(
            "integrations.rocketchat.delivery.post_json",
            lambda *_a, **_kw: DeliveryResponse(ok=False, error=leak_msg),
        )
        ok, error, message_id = post_rocketchat_message(_SERVER, "#c1", "hi", token, "u1")
        assert ok is False
        assert token not in error
        assert "<redacted>" in error
        assert message_id == ""

    def test_api_error_redacts_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        token = "rc-secret-token-abc123"
        monkeypatch.setattr(
            "integrations.rocketchat.delivery.post_json",
            lambda *_a, **_kw: DeliveryResponse(
                ok=True,
                status_code=401,
                data={"success": False, "error": f"bad token {token}"},
            ),
        )
        ok, error, _ = post_rocketchat_message(_SERVER, "#c1", "hi", token, "u1")
        assert ok is False
        assert token not in error
        assert "<redacted>" in error

    def test_exception_log_redacts_token(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        token = "rc-secret-token-abc123"
        leak_msg = f"connect failed with {token}"

        monkeypatch.setattr(
            "integrations.rocketchat.delivery.post_json",
            lambda *_a, **_kw: DeliveryResponse(ok=False, error=leak_msg),
        )
        with caplog.at_level(logging.WARNING, logger="integrations.rocketchat.delivery"):
            post_rocketchat_message(_SERVER, "#c1", "hi", token, "u1")

        joined = " ".join(rec.getMessage() for rec in caplog.records)
        assert token not in joined
        assert "<redacted>" in joined


# ---------------------------------------------------------------------------
# post_rocketchat_webhook
# ---------------------------------------------------------------------------

_WEBHOOK = f"{_SERVER}/hooks/hook-id/hook-token"


def test_post_webhook_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["url"] = url
        captured["json"] = json
        return _mock_response(200, {"success": True})

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _fake_post
    )
    attachments = [{"title": "Test"}]
    ok, error = post_rocketchat_webhook(_WEBHOOK, "hello", attachments=attachments)

    assert ok is True
    assert error == ""
    assert captured["url"] == _WEBHOOK
    assert captured["json"]["text"] == "hello"
    assert captured["json"]["attachments"] == attachments


def test_post_webhook_omits_attachments_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["json"] = json
        return _mock_response(200, {"success": True})

    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post", _fake_post
    )
    post_rocketchat_webhook(_WEBHOOK, "hello")
    assert "attachments" not in captured["json"]


def test_post_webhook_failure_returns_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post",
        lambda *_a, **_kw: _mock_response(400, {"success": False, "error": "Invalid integration"}),
    )
    ok, error = post_rocketchat_webhook(_WEBHOOK, "hello")
    assert ok is False
    assert "Invalid integration" in error


def test_post_webhook_http_200_but_success_false_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.delivery.notifications.delivery_transport.httpx.post",
        lambda *_a, **_kw: _mock_response(200, {"success": False}),
    )
    ok, _error = post_rocketchat_webhook(_WEBHOOK, "hello")
    assert ok is False


def test_post_webhook_exception_redacts_url(monkeypatch: pytest.MonkeyPatch) -> None:
    leak_msg = f"connect failed for {_WEBHOOK}"
    monkeypatch.setattr(
        "integrations.rocketchat.delivery.post_json",
        lambda *_a, **_kw: DeliveryResponse(ok=False, error=leak_msg),
    )
    ok, error = post_rocketchat_webhook(_WEBHOOK, "hello")
    assert ok is False
    assert _WEBHOOK not in error
    assert "<redacted>" in error


def test_post_webhook_error_body_redacts_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.rocketchat.delivery.post_json",
        lambda *_a, **_kw: DeliveryResponse(
            ok=True,
            status_code=404,
            data={"success": False, "error": f"no hook at {_WEBHOOK}"},
        ),
    )
    ok, error = post_rocketchat_webhook(_WEBHOOK, "hello")
    assert ok is False
    assert _WEBHOOK not in error
    assert "<redacted>" in error
