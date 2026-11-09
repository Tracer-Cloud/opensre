"""Catalog secret env loaders resolve via env then the credentials file."""

from __future__ import annotations

import pytest

import config.llm_credentials as llm_credentials
from integrations.catalog import load_env_integrations


@pytest.fixture
def local_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)


def _clear_secret_env(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    for name in names:
        monkeypatch.delenv(name, raising=False)


def test_grafana_token_loads_from_credentials_file(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    _clear_secret_env(monkeypatch, "GRAFANA_INSTANCE_URL", "GRAFANA_READ_TOKEN")
    monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://grafana.example.com")
    llm_credentials.save_credential("GRAFANA_READ_TOKEN", "glsa_from_store")
    records = load_env_integrations()
    grafana = next(r for r in records if r.get("service") == "grafana")
    assert grafana["credentials"]["api_key"] == "glsa_from_store"


def test_datadog_keys_env_win_over_credentials_file(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    _clear_secret_env(monkeypatch, "DD_API_KEY", "DD_APP_KEY", "DD_SITE")
    llm_credentials.save_credential("DD_API_KEY", "dd-stored")
    llm_credentials.save_credential("DD_APP_KEY", "dd-app-stored")
    monkeypatch.setenv("DD_API_KEY", "dd-env")
    monkeypatch.setenv("DD_APP_KEY", "dd-app-env")
    records = load_env_integrations()
    datadog = next(r for r in records if r.get("service") == "datadog")
    assert datadog["credentials"]["api_key"] == "dd-env"
    assert datadog["credentials"]["app_key"] == "dd-app-env"


def test_sentry_auth_token_loads_from_credentials_file(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    _clear_secret_env(monkeypatch, "SENTRY_ORG_SLUG", "SENTRY_AUTH_TOKEN")
    monkeypatch.setenv("SENTRY_ORG_SLUG", "acme")
    llm_credentials.save_credential("SENTRY_AUTH_TOKEN", "sentry-from-store")
    records = load_env_integrations()
    sentry = next(r for r in records if r.get("service") == "sentry")
    assert sentry["credentials"]["auth_token"] == "sentry-from-store"
