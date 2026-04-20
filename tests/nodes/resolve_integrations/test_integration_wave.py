"""Tests for integration-wave service normalization in resolve_integrations."""

from __future__ import annotations

import pytest

from app.nodes.resolve_integrations.node import _classify_integrations, _load_env_integrations


def test_classify_integrations_normalizes_bitbucket_and_new_services() -> None:
    integrations = [
        {
            "id": "bb-1",
            "service": "bitbucket",
            "status": "active",
            "credentials": {
                "workspace": "acme",
                "username": "bb-user",
                "app_password": "bb-pass",
                "base_url": "https://api.bitbucket.org/2.0/",
                "max_results": 30,
            },
        },
        {
            "id": "sf-1",
            "service": "snowflake",
            "status": "active",
            "credentials": {
                "account_identifier": "xy12345.us-east-1",
                "token": "sf-token",
                "warehouse": "COMPUTE_WH",
                "max_results": 70,
            },
        },
        {
            "id": "az-1",
            "service": "azure",
            "status": "active",
            "credentials": {
                "workspace_id": "workspace-1",
                "access_token": "azure-token",
                "endpoint": "https://api.loganalytics.io/",
            },
        },
        {
            "id": "oo-1",
            "service": "openobserve",
            "status": "active",
            "credentials": {
                "base_url": "https://oo.example.invalid/",
                "org": "acme",
                "api_token": "oo-token",
                "max_results": 80,
            },
        },
        {
            "id": "os-1",
            "service": "opensearch",
            "status": "active",
            "credentials": {
                "url": "https://os.example.invalid/",
                "api_key": "os-key",
                "index_pattern": "logs-*",
            },
        },
    ]

    resolved = _classify_integrations(integrations)

    assert resolved["bitbucket"]["workspace"] == "acme"
    assert resolved["bitbucket"]["integration_id"] == "bb-1"
    assert resolved["bitbucket"]["base_url"] == "https://api.bitbucket.org/2.0/"

    assert resolved["snowflake"]["account_identifier"] == "xy12345.us-east-1"
    assert resolved["snowflake"]["max_results"] == 70
    assert resolved["snowflake"]["integration_id"] == "sf-1"

    assert resolved["azure"]["workspace_id"] == "workspace-1"
    assert resolved["azure"]["endpoint"] == "https://api.loganalytics.io/"
    assert resolved["azure"]["integration_id"] == "az-1"

    assert resolved["openobserve"]["base_url"] == "https://oo.example.invalid"
    assert resolved["openobserve"]["org"] == "acme"
    assert resolved["openobserve"]["integration_id"] == "oo-1"

    assert resolved["opensearch"]["url"] == "https://os.example.invalid"
    assert resolved["opensearch"]["index_pattern"] == "logs-*"
    assert resolved["opensearch"]["integration_id"] == "os-1"


def test_classify_integrations_skips_snowflake_without_token() -> None:
    integrations = [
        {
            "id": "sf-no-token",
            "service": "snowflake",
            "status": "active",
            "credentials": {
                "account_identifier": "xy12345.us-east-1",
                "user": "service-user",
                "password": "secret",
            },
        }
    ]

    resolved = _classify_integrations(integrations)

    assert "snowflake" not in resolved


def test_load_env_integrations_reads_new_integration_wave(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BITBUCKET_WORKSPACE", "env-workspace")
    monkeypatch.setenv("BITBUCKET_USERNAME", "env-user")
    monkeypatch.setenv("BITBUCKET_APP_PASSWORD", "env-pass")

    monkeypatch.setenv("SNOWFLAKE_ACCOUNT_IDENTIFIER", "env-account")
    monkeypatch.setenv("SNOWFLAKE_TOKEN", "env-sf-token")

    monkeypatch.setenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID", "env-az-workspace")
    monkeypatch.setenv("AZURE_LOG_ANALYTICS_TOKEN", "env-az-token")

    monkeypatch.setenv("OPENOBSERVE_URL", "https://env-openobserve.example.invalid")
    monkeypatch.setenv("OPENOBSERVE_TOKEN", "env-oo-token")

    monkeypatch.setenv("OPENSEARCH_URL", "https://env-opensearch.example.invalid")
    monkeypatch.setenv("OPENSEARCH_API_KEY", "env-os-key")

    integrations = _load_env_integrations()
    by_service = {entry["service"]: entry for entry in integrations}

    assert by_service["bitbucket"]["credentials"]["workspace"] == "env-workspace"
    assert by_service["snowflake"]["credentials"]["account_identifier"] == "env-account"
    assert by_service["azure"]["credentials"]["workspace_id"] == "env-az-workspace"
    assert (
        by_service["openobserve"]["credentials"]["base_url"]
        == "https://env-openobserve.example.invalid"
    )
    assert (
        by_service["opensearch"]["credentials"]["url"] == "https://env-opensearch.example.invalid"
    )


def test_load_env_integrations_skips_snowflake_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT_IDENTIFIER", "env-account")
    monkeypatch.delenv("SNOWFLAKE_TOKEN", raising=False)
    monkeypatch.setenv("SNOWFLAKE_USER", "service-user")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret")

    integrations = _load_env_integrations()
    services = {entry["service"] for entry in integrations}

    assert "snowflake" not in services
