"""Tests for the SMTP integration: config, catalog, verifier."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from integrations.config_models import SMTPIntegrationConfig
from integrations.smtp import classify
from integrations.smtp.verifier import verify_smtp as _verify_smtp


def test_config_accepts_starttls_defaults() -> None:
    config = SMTPIntegrationConfig(
        host="smtp.example.com",
        from_address="opensre@example.com",
    )
    assert config.port == 587
    assert config.security == "starttls"


def test_config_rejects_invalid_security_mode() -> None:
    with pytest.raises(ValueError, match="security must be one of"):
        SMTPIntegrationConfig(
            host="smtp.example.com",
            from_address="opensre@example.com",
            security="tls",
        )


def test_config_rejects_partial_auth() -> None:
    with pytest.raises(ValueError, match="username and password"):
        SMTPIntegrationConfig(
            host="smtp.example.com",
            from_address="opensre@example.com",
            username="mailer",
        )


def test_verify_smtp_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.smtp.delivery.verify_smtp_connection",
        lambda _config: (True, "Connected to SMTP server successfully."),
    )

    result = _verify_smtp(
        "local env",
        {"host": "smtp.example.com", "from_address": "opensre@example.com"},
    )

    assert result["status"] == "passed"
    assert "connected" in result["detail"].lower()


def test_verify_smtp_reports_validation_errors() -> None:
    result = _verify_smtp("local env", {"host": "smtp.example.com"})
    assert result["status"] == "missing"
    assert "from_address" in result["detail"]


def test_classify_validation_failure_reports_without_secret_value() -> None:
    secret = "smtp-secret-password"

    with patch("infrastructure.observability.errors.boundary.capture_exception") as mock_report:
        result = classify(
            {
                "host": "smtp.example.com",
                "username": "mailer",
                "password": secret,
                "from_address": "not-an-email",
            },
            "smtp-record",
        )

    assert result == (None, None)
    mock_report.assert_called_once()
    reported_exc = mock_report.call_args.args[0]
    assert secret not in str(reported_exc)
    assert "smtp config validation failed" in str(reported_exc)


def test_catalog_bootstraps_smtp_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_SECURITY", "ssl")
    monkeypatch.setenv("SMTP_USERNAME", "mailer")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_FROM_ADDRESS", "opensre@example.com")
    monkeypatch.setenv("SMTP_DEFAULT_TO", "team@example.com")

    from integrations.catalog import resolve_effective_integrations

    effective = resolve_effective_integrations()

    assert "smtp" in effective
    smtp = effective["smtp"]["config"]
    assert smtp["host"] == "smtp.example.com"
    assert smtp["port"] == 465
    assert smtp["security"] == "ssl"
    assert smtp["username"] == "mailer"
    assert smtp["from_address"] == "opensre@example.com"
    assert smtp["default_to"] == "team@example.com"
