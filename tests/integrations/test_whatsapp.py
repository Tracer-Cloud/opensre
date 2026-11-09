"""Tests for WhatsApp integration config, catalog, and verification."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.config_models import WhatsAppConfig
from integrations.whatsapp.verifier import verify_whatsapp as _verify_whatsapp


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


def test_whatsapp_config_validates_required_fields() -> None:
    config = WhatsAppConfig(
        account_sid="AC1234567890",
        auth_token="tok-123",
        from_number="whatsapp:+14155238886",
        default_to="+1234567890",
    )

    assert config.account_sid == "AC1234567890"
    assert config.auth_token == "tok-123"
    assert config.from_number == "whatsapp:+14155238886"
    assert config.default_to == "+1234567890"


def test_whatsapp_config_rejects_empty_account_sid() -> None:
    with pytest.raises(ValueError, match="account_sid"):
        WhatsAppConfig(account_sid="   ", auth_token="tok", from_number="whatsapp:+1")


def test_whatsapp_config_rejects_empty_auth_token() -> None:
    with pytest.raises(ValueError, match="auth_token"):
        WhatsAppConfig(account_sid="AC123", auth_token="  ", from_number="whatsapp:+1")


def test_whatsapp_config_rejects_empty_from_number() -> None:
    with pytest.raises(ValueError, match="from_number"):
        WhatsAppConfig(account_sid="AC123", auth_token="tok", from_number="  ")


def test_whatsapp_config_default_to_optional() -> None:
    config = WhatsAppConfig(
        account_sid="AC123",
        auth_token="tok",
        from_number="whatsapp:+14155238886",
    )

    assert config.default_to is None


def test_verify_whatsapp_missing_account_sid() -> None:
    result = _verify_whatsapp("env", {"auth_token": "tok"})

    assert result["status"] == "missing"
    assert "account_sid" in result["detail"].lower()


def test_verify_whatsapp_missing_auth_token() -> None:
    result = _verify_whatsapp("env", {"account_sid": "AC123"})

    assert result["status"] == "missing"
    assert "auth_token" in result["detail"].lower()


def test_verify_whatsapp_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_get(*args: Any, **kwargs: Any) -> Any:
        return _FakeResponse({"friendly_name": "Demo Account", "sid": "AC123"})

    monkeypatch.setattr("integrations.whatsapp.verifier.requests.get", _fake_get)

    result = _verify_whatsapp("env", {"account_sid": "AC123", "auth_token": "tok"})

    assert result["status"] == "passed"
    assert "Demo Account" in result["detail"]


def test_verify_whatsapp_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "whatsapp-auth-token"

    def _fake_get(*args: Any, **kwargs: Any) -> Any:
        raise Exception(f"Connection timeout for {token}")

    monkeypatch.setattr("integrations.whatsapp.verifier.requests.get", _fake_get)

    result = _verify_whatsapp("env", {"account_sid": "AC123", "auth_token": "tok"})

    assert result["status"] == "failed"
    assert "Connection timeout" in result["detail"]
    assert token not in result["detail"]
    assert "<redacted>" in result["detail"]


def test_verify_whatsapp_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_get(*args: Any, **kwargs: Any) -> Any:
        return _FakeResponse({}, status_code=401)

    monkeypatch.setattr("integrations.whatsapp.verifier.requests.get", _fake_get)

    result = _verify_whatsapp("env", {"account_sid": "AC123", "auth_token": "tok"})

    assert result["status"] == "failed"


def test_catalog_resolve_whatsapp_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    monkeypatch.setenv("WHATSAPP_DEFAULT_TO", "+1234567890")

    from integrations.catalog import resolve_effective_integrations

    effective = resolve_effective_integrations()

    assert "whatsapp" in effective
    assert effective["whatsapp"]["source"] == "local env"
    assert effective["whatsapp"]["config"]["account_sid"] == "AC123"
    assert effective["whatsapp"]["config"]["auth_token"] == "tok"
    assert effective["whatsapp"]["config"]["from_number"] == "whatsapp:+14155238886"
    assert effective["whatsapp"]["config"]["default_to"] == "+1234567890"


def test_catalog_skips_whatsapp_when_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWILIO_WHATSAPP_FROM", raising=False)

    from integrations.catalog import resolve_effective_integrations

    effective = resolve_effective_integrations()

    assert "whatsapp" not in effective
