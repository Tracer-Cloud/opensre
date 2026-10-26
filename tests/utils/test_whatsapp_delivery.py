"""Tests for integrations.whatsapp.delivery — WhatsApp via Twilio transport."""

from __future__ import annotations

from typing import Any

import pytest

from infrastructure.delivery.notifications.delivery_transport import DeliveryResponse
from integrations.whatsapp.delivery import post_whatsapp_message_twilio


def _success_response(**kwargs: Any) -> DeliveryResponse:
    defaults: dict[str, Any] = {"ok": True, "status_code": 201, "data": {"sid": "SM123"}}
    defaults.update(kwargs)
    return DeliveryResponse(**defaults)


def _error_response(**kwargs: Any) -> DeliveryResponse:
    defaults: dict[str, Any] = {
        "ok": True,
        "status_code": 400,
        "data": {"message": "Invalid 'From' parameter"},
        "text": "Bad Request",
    }
    defaults.update(kwargs)
    return DeliveryResponse(**defaults)


def test_post_whatsapp_message_twilio_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post_form(url: str, data: dict[str, str], **kwargs: Any) -> DeliveryResponse:
        captured["url"] = url
        captured["data"] = data
        captured.update(kwargs)
        return _success_response()

    monkeypatch.setattr("integrations.whatsapp.delivery.post_form", _fake_post_form)

    success, error, message_id = post_whatsapp_message_twilio(
        to="+1234567890",
        text="hello",
        account_sid="AC123",
        auth_token="secret",
        from_number="whatsapp:+14155238886",
    )

    assert success is True
    assert error == ""
    assert message_id == "SM123"
    assert captured["url"].endswith("/Accounts/AC123/Messages.json")
    assert captured["data"]["From"] == "whatsapp:+14155238886"
    assert captured["data"]["To"] == "whatsapp:+1234567890"
    assert captured["data"]["Body"] == "hello"
    assert captured["auth"] == ("AC123", "secret")


def test_post_whatsapp_message_twilio_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.whatsapp.delivery.post_form",
        lambda *_a, **_kw: DeliveryResponse(
            ok=False, error="auth header tok-leak failed", exc_type="RuntimeError"
        ),
    )

    success, error, message_id = post_whatsapp_message_twilio(
        to="+123",
        text="test",
        account_sid="AC123",
        auth_token="tok-leak",
        from_number="whatsapp:+14155238886",
    )

    assert success is False
    assert "tok-leak" not in error
    assert "<redacted>" in error
    assert message_id == ""


def test_post_whatsapp_message_twilio_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.whatsapp.delivery.post_form",
        lambda *_a, **_kw: _error_response(),
    )

    success, error, message_id = post_whatsapp_message_twilio(
        to="+123",
        text="Test",
        account_sid="AC123",
        auth_token="tok-123",
        from_number="whatsapp:bad",
    )

    assert success is False
    assert "Invalid 'From' parameter" in error
    assert message_id == ""


def test_post_whatsapp_message_twilio_prefixes_whatsapp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_post_form(url: str, data: dict[str, str], **kwargs: Any) -> DeliveryResponse:
        captured["data"] = data
        return _success_response()

    monkeypatch.setattr("integrations.whatsapp.delivery.post_form", _fake_post_form)

    post_whatsapp_message_twilio(
        to="+1234567890",
        text="hi",
        account_sid="AC123",
        auth_token="tok",
        from_number="+14155238886",
    )

    assert captured["data"]["To"] == "whatsapp:+1234567890"
    assert captured["data"]["From"] == "whatsapp:+14155238886"


class TestWhatsAppDelegatesToSharedTransport:
    """WhatsApp delivery must use post_form from delivery_transport,
    not import httpx directly."""

    def test_module_does_not_import_httpx(self) -> None:
        from integrations.whatsapp import delivery as whatsapp_delivery

        assert not hasattr(whatsapp_delivery, "httpx"), (
            "whatsapp_delivery should not import httpx directly — "
            "it must go through delivery_transport.post_form"
        )
