"""Tests for the Twilio SMS integration: config, catalog, verifier."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.config_models import TwilioIntegrationConfig, TwilioSMSChannelConfig
from integrations.twilio.verifier import (
    TwilioFailureKind,
    build_twilio_config,
    validate_twilio_config,
)
from integrations.twilio.verifier import (
    verify_twilio as _verify_twilio,
)


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


# ---- TwilioIntegrationConfig --------------------------------------------------


def test_config_accepts_sms_with_from_number() -> None:
    config = TwilioIntegrationConfig(
        account_sid="AC1",
        auth_token="tok",
        sms=TwilioSMSChannelConfig(enabled=True, from_number="+14155551111"),
    )
    assert config.configured_channels == ["sms"]


def test_config_accepts_sms_via_messaging_service_sid() -> None:
    config = TwilioIntegrationConfig(
        account_sid="AC1",
        auth_token="tok",
        sms=TwilioSMSChannelConfig(enabled=True, messaging_service_sid="MG1"),
    )
    assert config.configured_channels == ["sms"]


def test_config_rejects_missing_sms_channel() -> None:
    with pytest.raises(ValueError, match="SMS channel configured"):
        TwilioIntegrationConfig(
            account_sid="AC1",
            auth_token="tok",
            sms=TwilioSMSChannelConfig(enabled=False),
        )


def test_config_rejects_enabled_sms_without_sender() -> None:
    with pytest.raises(ValueError, match="SMS channel configured"):
        TwilioIntegrationConfig(
            account_sid="AC1",
            auth_token="tok",
            sms=TwilioSMSChannelConfig(enabled=True, from_number=""),
        )


def test_config_rejects_blank_account_sid() -> None:
    with pytest.raises(ValueError, match="account_sid"):
        TwilioIntegrationConfig(
            account_sid="   ",
            auth_token="tok",
            sms=TwilioSMSChannelConfig(enabled=True, from_number="+1"),
        )


def test_config_rejects_blank_auth_token() -> None:
    with pytest.raises(ValueError, match="auth_token"):
        TwilioIntegrationConfig(
            account_sid="AC1",
            auth_token="  ",
            sms=TwilioSMSChannelConfig(enabled=True, from_number="+1"),
        )


# ---- build_twilio_config / validate_twilio_config -----------------------------


def test_build_twilio_config_raises_for_missing_account_sid() -> None:
    with pytest.raises(ValueError, match="Missing account_sid"):
        build_twilio_config({"auth_token": "tok"})


def test_build_twilio_config_raises_for_missing_auth_token() -> None:
    with pytest.raises(ValueError, match="Missing auth_token"):
        build_twilio_config({"account_sid": "AC1"})


def test_validate_twilio_config_api_error_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "twilio-auth-token"

    def _raise(*_a: Any, **_kw: Any) -> Any:
        raise Exception(f"Connection timeout for {token}")

    monkeypatch.setattr("integrations.twilio.verifier.requests.get", _raise)

    outcome = validate_twilio_config(
        build_twilio_config({"account_sid": "AC1", "auth_token": "tok"})
    )

    assert outcome.ok is False
    assert outcome.failure_kind is TwilioFailureKind.API_ERROR
    assert token not in outcome.detail
    assert "<redacted>" in outcome.detail


def test_validate_twilio_config_sms_not_ready_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.twilio.verifier.requests.get",
        lambda *_a, **_kw: _FakeResponse({"friendly_name": "Demo"}),
    )

    outcome = validate_twilio_config(
        build_twilio_config(
            {
                "account_sid": "AC1",
                "auth_token": "tok",
                "sms": {"enabled": False, "from_number": ""},
            }
        )
    )

    assert outcome.ok is False
    assert outcome.failure_kind is TwilioFailureKind.SMS_NOT_READY


# ---- _verify_twilio -----------------------------------------------------------


def test_verify_missing_account_sid() -> None:
    result = _verify_twilio("env", {"auth_token": "tok"})
    assert result["status"] == "missing"
    assert "account_sid" in result["detail"].lower()


def test_verify_missing_auth_token() -> None:
    result = _verify_twilio("env", {"account_sid": "AC1"})
    assert result["status"] == "missing"
    assert "auth_token" in result["detail"].lower()


def test_verify_passed_when_sms_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.twilio.verifier.requests.get",
        lambda *_a, **_kw: _FakeResponse({"friendly_name": "Demo"}),
    )

    result = _verify_twilio(
        "env",
        {
            "account_sid": "AC1",
            "auth_token": "tok",
            "sms": {"enabled": True, "from_number": "+14155551111"},
        },
    )

    assert result["status"] == "passed"
    assert "sms" in result["detail"].lower()


def test_verify_passed_with_messaging_service_sid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.twilio.verifier.requests.get",
        lambda *_a, **_kw: _FakeResponse({"friendly_name": "Demo"}),
    )

    result = _verify_twilio(
        "env",
        {
            "account_sid": "AC1",
            "auth_token": "tok",
            "sms": {"enabled": True, "messaging_service_sid": "MG1"},
        },
    )

    assert result["status"] == "passed"


def test_verify_failed_when_sms_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.twilio.verifier.requests.get",
        lambda *_a, **_kw: _FakeResponse({"friendly_name": "Demo"}),
    )

    result = _verify_twilio(
        "env",
        {
            "account_sid": "AC1",
            "auth_token": "tok",
            "sms": {"enabled": False, "from_number": ""},
        },
    )

    assert result["status"] == "failed"
    assert "sms channel is not ready" in result["detail"].lower()


def test_verify_failed_when_api_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: Any, **_kw: Any) -> Any:
        raise Exception("Connection timeout")

    monkeypatch.setattr("integrations.twilio.verifier.requests.get", _raise)

    result = _verify_twilio("env", {"account_sid": "AC1", "auth_token": "tok"})

    assert result["status"] == "failed"
    assert "Connection timeout" in result["detail"]


# ---- Catalog env-bootstrap ----------------------------------------------------


def test_catalog_bootstraps_twilio_from_env_with_sms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_SMS_FROM", "+14155551111")
    monkeypatch.setenv("TWILIO_SMS_DEFAULT_TO", "+14155550000")

    from integrations.catalog import resolve_effective_integrations

    effective = resolve_effective_integrations()

    assert "twilio" in effective
    twilio = effective["twilio"]["config"]
    assert twilio["account_sid"] == "AC1"
    assert twilio["sms"]["enabled"] is True
    assert twilio["sms"]["from_number"] == "+14155551111"
    assert twilio["sms"]["default_to"] == "+14155550000"
    assert "whatsapp" not in twilio


def test_catalog_bootstraps_legacy_whatsapp_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy ``whatsapp`` record is unaffected by the Twilio SMS integration."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    monkeypatch.delenv("TWILIO_SMS_FROM", raising=False)
    monkeypatch.delenv("TWILIO_SMS_MESSAGING_SERVICE_SID", raising=False)

    from integrations.catalog import resolve_effective_integrations

    effective = resolve_effective_integrations()

    assert "whatsapp" in effective
    # WhatsApp-only env does NOT create a twilio record (SMS sender absent).
    assert "twilio" not in effective


def test_catalog_skips_twilio_without_sms_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.delenv("TWILIO_SMS_FROM", raising=False)
    monkeypatch.delenv("TWILIO_SMS_MESSAGING_SERVICE_SID", raising=False)

    from integrations.catalog import resolve_effective_integrations

    effective = resolve_effective_integrations()

    assert "twilio" not in effective
