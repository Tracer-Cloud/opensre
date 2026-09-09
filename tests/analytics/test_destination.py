"""Tests for first-party analytics endpoint and authentication resolution."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import replace
from pathlib import Path

from config.account import AccountRecord
from infrastructure.analytics import destination


def _account() -> AccountRecord:
    return AccountRecord(
        user_id="user_123",
        organization_id="org_123",
        email="user@example.com",
        app_url="http://localhost:3000",
        signed_in_at="2026-09-08T10:00:00+00:00",
        token_expires_at="2026-12-08T10:00:00+00:00",
    )


def test_anonymous_install_uses_first_party_production_endpoint(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_APP_URL", raising=False)
    monkeypatch.delenv("OPENSRE_WEBAPP_URL", raising=False)
    monkeypatch.delenv("AGENT_USAGE_SECRET", raising=False)
    monkeypatch.setattr(destination, "load_account_record", lambda: None)
    monkeypatch.setattr(destination, "account_metadata_path", lambda: tmp_path / "account.json")

    resolved = destination.resolve_analytics_destination()

    assert resolved is not None
    assert resolved.endpoint_url == "https://app.opensre.com/api/analytics/events"
    assert resolved.headers() == {"Accept": "application/json"}


def test_signed_in_cli_uses_account_origin_and_bearer_token(monkeypatch) -> None:
    monkeypatch.delenv("OPENSRE_WEBAPP_URL", raising=False)
    monkeypatch.delenv("OPENSRE_ACCOUNT_TOKEN", raising=False)
    monkeypatch.setattr(destination, "load_account_record", _account)
    monkeypatch.setattr(destination, "resolve_account_token", lambda: "osre_pat_secret")

    resolved = destination.resolve_analytics_destination()

    assert resolved is not None
    assert resolved.endpoint_url == "http://localhost:3000/api/analytics/events"
    assert resolved.headers() == {
        "Accept": "application/json",
        "Authorization": "Bearer osre_pat_secret",
    }


def test_authenticated_payload_is_signed_without_putting_key_in_body() -> None:
    resolved = destination.AnalyticsDestination(
        endpoint_url="https://app.opensre.com/api/analytics/events",
        bearer_token="osre_pat_secret",
    )
    body = b'{"event":"cli_invoked"}'

    headers = resolved.headers(body, timestamp=1_788_880_000)

    expected = hmac.new(
        b"osre_pat_secret",
        b'1788880000.{"event":"cli_invoked"}',
        hashlib.sha256,
    ).hexdigest()
    assert headers["X-OpenSRE-Timestamp"] == "1788880000"
    assert headers["X-OpenSRE-Signature"] == f"v1={expected}"
    assert b"osre_pat_secret" not in body


def test_silo_destination_takes_precedence_over_personal_account(monkeypatch) -> None:
    monkeypatch.setenv("OPENSRE_WEBAPP_URL", "https://webapp.internal.example/")
    monkeypatch.setenv("AGENT_USAGE_SECRET", "agent-secret")

    def _should_not_load_account() -> AccountRecord:
        raise AssertionError("silo analytics must not read a personal account")

    monkeypatch.setattr(destination, "load_account_record", _should_not_load_account)

    resolved = destination.resolve_analytics_destination()

    assert resolved is not None
    assert resolved.endpoint_url == "https://webapp.internal.example/api/analytics/events"
    assert resolved.headers()["Authorization"] == "Bearer agent-secret"


def test_invalid_silo_url_never_forwards_shared_secret(monkeypatch) -> None:
    monkeypatch.setenv("OPENSRE_WEBAPP_URL", "ftp://invalid.example")
    monkeypatch.setenv("AGENT_USAGE_SECRET", "must-not-leak")
    monkeypatch.delenv("OPENSRE_APP_URL", raising=False)

    resolved = destination.resolve_analytics_destination()

    assert resolved is None


def test_silo_without_shared_secret_disables_delivery(monkeypatch) -> None:
    monkeypatch.setenv("OPENSRE_WEBAPP_URL", "https://webapp.internal.example/")
    monkeypatch.delenv("AGENT_USAGE_SECRET", raising=False)

    resolved = destination.resolve_analytics_destination()

    assert resolved is None


def test_invalid_account_url_never_forwards_personal_token(monkeypatch) -> None:
    invalid_account = replace(_account(), app_url="ftp://invalid.example")
    monkeypatch.delenv("OPENSRE_WEBAPP_URL", raising=False)
    monkeypatch.delenv("OPENSRE_APP_URL", raising=False)
    monkeypatch.setattr(destination, "load_account_record", lambda: invalid_account)

    def _should_not_resolve_token() -> str:
        raise AssertionError("invalid account origin must not resolve its credential")

    monkeypatch.setattr(destination, "resolve_account_token", _should_not_resolve_token)

    resolved = destination.resolve_analytics_destination()

    assert resolved is None


def test_insecure_remote_account_url_never_forwards_personal_token(monkeypatch) -> None:
    insecure_account = replace(_account(), app_url="http://analytics.example")
    monkeypatch.delenv("OPENSRE_WEBAPP_URL", raising=False)
    monkeypatch.delenv("OPENSRE_APP_URL", raising=False)
    monkeypatch.setattr(destination, "load_account_record", lambda: insecure_account)

    def _should_not_resolve_token() -> str:
        raise AssertionError("bearer credentials require TLS outside loopback")

    monkeypatch.setattr(destination, "resolve_account_token", _should_not_resolve_token)

    resolved = destination.resolve_analytics_destination()

    assert resolved is None


def test_account_without_bearer_token_disables_delivery(monkeypatch) -> None:
    monkeypatch.delenv("OPENSRE_WEBAPP_URL", raising=False)
    monkeypatch.setattr(destination, "load_account_record", _account)
    monkeypatch.setattr(destination, "resolve_account_token", lambda: "")

    resolved = destination.resolve_analytics_destination()

    assert resolved is None


def test_account_read_failure_disables_delivery(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENSRE_WEBAPP_URL", raising=False)
    account_path = tmp_path / "account.json"
    account_path.write_text("malformed", encoding="utf-8")
    monkeypatch.setattr(destination, "account_metadata_path", lambda: account_path)
    monkeypatch.setattr(destination, "load_account_record", lambda: None)

    resolved = destination.resolve_analytics_destination()

    assert resolved is None


def test_conflicting_environment_account_token_disables_delivery(monkeypatch) -> None:
    monkeypatch.delenv("OPENSRE_WEBAPP_URL", raising=False)
    monkeypatch.setenv("OPENSRE_ACCOUNT_TOKEN", "osre_pat_other_account")
    monkeypatch.setattr(destination, "load_account_record", _account)
    monkeypatch.setattr(destination, "resolve_account_token", lambda: "osre_pat_other_account")
    monkeypatch.setattr(destination, "stored_account_token", lambda: "osre_pat_saved_login")
    monkeypatch.setattr(destination, "keyring_is_disabled", lambda: False)

    resolved = destination.resolve_analytics_destination()

    assert resolved is None


def test_environment_only_account_token_is_supported(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENSRE_WEBAPP_URL", raising=False)
    monkeypatch.setenv("OPENSRE_ACCOUNT_TOKEN", "osre_pat_environment_only")
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OPENSRE_APP_URL", "http://localhost:3000")
    monkeypatch.setattr(destination, "load_account_record", lambda: None)
    monkeypatch.setattr(destination, "account_metadata_path", lambda: tmp_path / "account.json")

    resolved = destination.resolve_analytics_destination()

    assert resolved is not None
    assert resolved.endpoint_url == "http://localhost:3000/api/analytics/events"
    assert resolved.headers()["Authorization"] == "Bearer osre_pat_environment_only"


def test_environment_only_account_token_requires_explicit_origin(monkeypatch) -> None:
    monkeypatch.delenv("OPENSRE_WEBAPP_URL", raising=False)
    monkeypatch.delenv("OPENSRE_APP_URL", raising=False)
    monkeypatch.setenv("OPENSRE_ACCOUNT_TOKEN", "osre_pat_environment_only")
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")
    monkeypatch.setattr(destination, "load_account_record", _account)

    resolved = destination.resolve_analytics_destination()

    assert resolved is None


def test_invalid_anonymous_override_disables_delivery(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENSRE_WEBAPP_URL", raising=False)
    monkeypatch.setenv("OPENSRE_APP_URL", "ftp://invalid.example")
    monkeypatch.setattr(destination, "load_account_record", lambda: None)
    monkeypatch.setattr(destination, "account_metadata_path", lambda: tmp_path / "account.json")

    resolved = destination.resolve_analytics_destination()

    assert resolved is None
