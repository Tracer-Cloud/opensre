"""Tests for integrations.twilio.delivery — Twilio SMS transport."""

from __future__ import annotations

from typing import Any

import pytest

from infrastructure.delivery.notifications.delivery_transport import DeliveryResponse
from integrations.twilio.delivery import post_twilio_sms, send_twilio_sms_report


def _success_response(**kwargs: Any) -> DeliveryResponse:
    defaults: dict[str, Any] = {"ok": True, "status_code": 201, "data": {"sid": "SM1"}}
    defaults.update(kwargs)
    return DeliveryResponse(**defaults)


def _error_response(**kwargs: Any) -> DeliveryResponse:
    defaults: dict[str, Any] = {
        "ok": True,
        "status_code": 400,
        "data": {"message": "Invalid 'To' parameter"},
        "text": "Bad Request",
    }
    defaults.update(kwargs)
    return DeliveryResponse(**defaults)


def test_post_twilio_sms_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post_form(url: str, data: dict[str, str], **kwargs: Any) -> DeliveryResponse:
        captured["url"] = url
        captured["data"] = data
        captured.update(kwargs)
        return _success_response()

    monkeypatch.setattr("integrations.twilio.delivery.post_form", _fake_post_form)

    success, error, sid = post_twilio_sms(
        to="+14155550000",
        text="ping",
        account_sid="AC1",
        auth_token="tok",
        from_number="+14155551111",
    )

    assert (success, error, sid) == (True, "", "SM1")
    assert captured["url"].endswith("/Accounts/AC1/Messages.json")
    assert captured["data"]["To"] == "+14155550000"
    assert captured["data"]["From"] == "+14155551111"
    assert captured["data"]["Body"] == "ping"
    assert "MessagingServiceSid" not in captured["data"]
    assert "StatusCallback" not in captured["data"]
    assert captured["auth"] == ("AC1", "tok")


def test_post_twilio_sms_with_messaging_service_overrides_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_post_form(url: str, data: dict[str, str], **kwargs: Any) -> DeliveryResponse:
        captured["data"] = data
        return _success_response(data={"sid": "SM2"})

    monkeypatch.setattr("integrations.twilio.delivery.post_form", _fake_post_form)

    post_twilio_sms(
        to="+14155550000",
        text="hi",
        account_sid="AC1",
        auth_token="tok",
        from_number="+14155551111",
        messaging_service_sid="MG123",
    )

    assert captured["data"]["MessagingServiceSid"] == "MG123"
    assert "From" not in captured["data"]


def test_post_twilio_sms_status_callback_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_post_form(url: str, data: dict[str, str], **kwargs: Any) -> DeliveryResponse:
        captured["data"] = data
        return _success_response(data={"sid": "SM3"})

    monkeypatch.setattr("integrations.twilio.delivery.post_form", _fake_post_form)

    post_twilio_sms(
        to="+14155550000",
        text="hi",
        account_sid="AC1",
        auth_token="tok",
        from_number="+14155551111",
        status_callback="https://example.com/webhooks/twilio/status",
    )

    assert captured["data"]["StatusCallback"] == "https://example.com/webhooks/twilio/status"


def test_post_twilio_sms_missing_sender_fails() -> None:
    success, error, sid = post_twilio_sms(
        to="+14155550000",
        text="hi",
        account_sid="AC1",
        auth_token="tok",
    )

    assert success is False
    assert "from_number" in error
    assert sid == ""


def test_post_twilio_sms_transport_failure_redacts_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.twilio.delivery.post_form",
        lambda *_a, **_kw: DeliveryResponse(
            ok=False, error="auth header tok-leak failed", exc_type="RuntimeError"
        ),
    )

    success, error, sid = post_twilio_sms(
        to="+14155550000",
        text="hi",
        account_sid="AC1",
        auth_token="tok-leak",
        from_number="+14155551111",
    )

    assert success is False
    assert "tok-leak" not in error
    assert "<redacted>" in error
    assert sid == ""


def test_post_twilio_sms_api_error_returns_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.twilio.delivery.post_form",
        lambda *_a, **_kw: _error_response(),
    )

    success, error, sid = post_twilio_sms(
        to="bad",
        text="hi",
        account_sid="AC1",
        auth_token="tok",
        from_number="+14155551111",
    )

    assert success is False
    assert "Invalid 'To' parameter" in error
    assert sid == ""


def test_post_twilio_sms_api_error_message_key_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Twilio sometimes uses error_message instead of message."""
    monkeypatch.setattr(
        "integrations.twilio.delivery.post_form",
        lambda *_a, **_kw: DeliveryResponse(
            ok=True,
            status_code=400,
            data={"error_message": "Queue overflow"},
            text="",
        ),
    )

    success, error, sid = post_twilio_sms(
        to="+14155550000",
        text="hi",
        account_sid="AC1",
        auth_token="tok",
        from_number="+14155551111",
    )

    assert success is False
    assert "Queue overflow" in error
    assert sid == ""


def test_send_twilio_sms_report_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post_form(url: str, data: dict[str, str], **kwargs: Any) -> DeliveryResponse:
        captured["data"] = data
        return _success_response(data={"sid": "SM6"})

    monkeypatch.setattr("integrations.twilio.delivery.post_form", _fake_post_form)

    send_twilio_sms_report(
        report="X" * 5000,
        sms_ctx={
            "account_sid": "AC1",
            "auth_token": "tok",
            "from_number": "+14155551111",
            "to": "+14155550000",
        },
    )

    assert len(captured["data"]["Body"]) <= 1600
    assert captured["data"]["Body"].endswith("…")


def test_send_twilio_sms_report_missing_creds() -> None:
    success, error, sid = send_twilio_sms_report(
        report="hi",
        sms_ctx={"account_sid": "AC1"},
    )

    assert success is False
    assert "Missing" in error
    assert sid == ""


def test_send_twilio_sms_report_missing_sender() -> None:
    success, error, sid = send_twilio_sms_report(
        report="hi",
        sms_ctx={
            "account_sid": "AC1",
            "auth_token": "tok",
            "to": "+14155550000",
        },
    )

    assert success is False
    assert "from_number" in error
    assert sid == ""


def test_send_twilio_sms_report_success_returns_sid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.twilio.delivery.post_form",
        lambda *_a, **_kw: _success_response(data={"sid": "SM-OK"}),
    )

    success, error, sid = send_twilio_sms_report(
        report="investigation summary",
        sms_ctx={
            "account_sid": "AC1",
            "auth_token": "tok",
            "messaging_service_sid": "MG1",
            "to": "+14155550000",
        },
    )

    assert success is True
    assert error == ""
    assert sid == "SM-OK"


class TestTwilioDelegatesToSharedTransport:
    """Twilio delivery must use post_form from delivery_transport,
    not import httpx directly."""

    def test_module_does_not_import_httpx(self) -> None:
        from integrations.twilio import delivery as twilio_delivery

        assert not hasattr(twilio_delivery, "httpx"), (
            "twilio_delivery should not import httpx directly — "
            "it must go through delivery_transport.post_form"
        )
