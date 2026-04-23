"""Tests for Splunk integration config, catalog, and verification."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.integrations.models import SplunkIntegrationConfig
from app.services.splunk.client import SplunkClient, SplunkConfig, build_splunk_spl_query

# ── SplunkIntegrationConfig ───────────────────────────────────────────────────

def test_config_normalizes_trailing_slash() -> None:
    config = SplunkIntegrationConfig(base_url="https://splunk.corp.com/", token="tok")
    assert not config.base_url.endswith("/")


def test_config_default_index_is_main() -> None:
    config = SplunkIntegrationConfig(base_url="https://splunk:8089", token="tok")
    assert config.index == "main"


def test_config_custom_index() -> None:
    config = SplunkIntegrationConfig(base_url="https://splunk:8089", token="tok", index="prod")
    assert config.index == "prod"


def test_config_strips_whitespace() -> None:
    config = SplunkIntegrationConfig(
        base_url="  https://splunk:8089  ",
        token="  tok  ",
    )
    assert config.base_url == "https://splunk:8089"
    assert config.token == "tok"


def test_config_empty_index_defaults_to_main() -> None:
    config = SplunkIntegrationConfig(base_url="https://splunk:8089", token="tok", index="")
    assert config.index == "main"


def test_config_verify_ssl_defaults_to_true() -> None:
    config = SplunkIntegrationConfig(base_url="https://splunk:8089", token="tok")
    assert config.verify_ssl is True


def test_config_rejects_unknown_fields() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SplunkIntegrationConfig(
            base_url="https://splunk:8089",
            token="tok",
            unknown_field="oops",  # type: ignore[call-arg]
        )


# ── SplunkConfig ──────────────────────────────────────────────────────────────

def test_splunk_config_strips_trailing_slash() -> None:
    cfg = SplunkConfig(base_url="https://splunk:8089/", token="tok")
    assert not cfg.base_url.endswith("/")


def test_splunk_config_is_configured() -> None:
    assert SplunkConfig(base_url="https://splunk:8089", token="tok").is_configured is True
    assert SplunkConfig(base_url="", token="tok").is_configured is False
    assert SplunkConfig(base_url="https://splunk:8089", token="").is_configured is False


# ── build_splunk_spl_query ────────────────────────────────────────────────────

def test_build_query_returns_raw_query_verbatim_when_supplied() -> None:
    spl = build_splunk_spl_query(
        raw_query='index=prod "PaymentTimeout" | head 20',
        index="main",
    )
    assert 'index=prod "PaymentTimeout"' in spl
    assert "| head 20" in spl


def test_build_query_appends_head_to_raw_query_without_one() -> None:
    spl = build_splunk_spl_query(raw_query='index=prod "error"', index="main", limit=30)
    assert "| head 30" in spl


def test_build_query_does_not_duplicate_head_clause() -> None:
    spl = build_splunk_spl_query(
        raw_query='index=prod "error" | head 10',
        index="main",
        limit=50,
    )
    assert spl.count("| head") == 1


def test_build_query_from_error_message() -> None:
    spl = build_splunk_spl_query(
        index="main",
        error_message="NullPointerException",
        limit=50,
    )
    assert "index=main" in spl
    assert '"NullPointerException"' in spl
    assert "| head 50" in spl


def test_build_query_from_trace_id() -> None:
    spl = build_splunk_spl_query(index="prod", trace_id="abc-123", limit=50)
    assert 'trace_id="abc-123"' in spl
    assert "index=prod" in spl


def test_build_query_trace_id_takes_priority_over_error_message() -> None:
    spl = build_splunk_spl_query(
        index="main",
        error_message="SomeError",
        trace_id="trace-xyz",
        limit=50,
    )
    assert 'trace_id="trace-xyz"' in spl
    assert "SomeError" not in spl


def test_build_query_fallback_when_no_signals() -> None:
    spl = build_splunk_spl_query(index="main", limit=50)
    assert "index=main" in spl
    assert "| head 50" in spl


def test_build_query_from_alert_name_when_no_error_message() -> None:
    spl = build_splunk_spl_query(index="main", alert_name="payments-error-spike", limit=50)
    assert '"payments-error-spike"' in spl


def test_build_query_escapes_double_quotes_in_keyword() -> None:
    spl = build_splunk_spl_query(index="main", error_message='say "hello"', limit=10)
    assert '\\"hello\\"' in spl


# ── SplunkClient.validate_access ─────────────────────────────────────────────

def test_validate_access_success() -> None:
    config = SplunkConfig(base_url="https://splunk:8089", token="tok")
    client = SplunkClient(config)
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "entry": [{"content": {"version": "9.1.0"}}]
    }
    with patch("app.services.splunk.client.httpx.get", return_value=mock_response):
        result = client.validate_access()
    assert result["success"] is True
    assert "9.1.0" in result["detail"]


def test_validate_access_http_error() -> None:
    config = SplunkConfig(base_url="https://splunk:8089", token="bad-token")
    client = SplunkClient(config)
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"
    with patch(
        "app.services.splunk.client.httpx.get",
        side_effect=httpx.HTTPStatusError("", request=MagicMock(), response=mock_response),
    ):
        result = client.validate_access()
    assert result["success"] is False
    assert "401" in result["error"]


def test_validate_access_connection_error() -> None:
    config = SplunkConfig(base_url="https://splunk:8089", token="tok")
    client = SplunkClient(config)
    with patch(
        "app.services.splunk.client.httpx.get",
        side_effect=Exception("Connection refused"),
    ):
        result = client.validate_access()
    assert result["success"] is False
    assert "Connection refused" in result["error"]


# ── SplunkClient.search_logs ──────────────────────────────────────────────────

def test_search_logs_prepends_search_keyword_if_missing() -> None:
    config = SplunkConfig(base_url="https://splunk:8089", token="tok")
    client = SplunkClient(config)
    captured = {}

    def fake_post(url, *, headers, data, timeout, verify):
        captured["search"] = data["search"]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = ""
        return mock_resp

    with patch("app.services.splunk.client.httpx.post", side_effect=fake_post):
        client.search_logs(query='index=main "error" | head 10')

    assert captured["search"].startswith("search ")


def test_search_logs_does_not_double_prepend_search_keyword() -> None:
    config = SplunkConfig(base_url="https://splunk:8089", token="tok")
    client = SplunkClient(config)
    captured = {}

    def fake_post(url, *, headers, data, timeout, verify):
        captured["search"] = data["search"]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = ""
        return mock_resp

    with patch("app.services.splunk.client.httpx.post", side_effect=fake_post):
        client.search_logs(query='search index=main "error" | head 10')

    assert captured["search"].count("search ") == 1


def test_search_logs_returns_parsed_ndjson_results() -> None:
    config = SplunkConfig(base_url="https://splunk:8089", token="tok")
    client = SplunkClient(config)

    ndjson_response = (
        '{"result": {"_time": "2024-01-01T00:00:00Z", "_raw": "NullPointerException", "host": "web-01"}}\n'
        '{"result": {"_time": "2024-01-01T00:00:01Z", "_raw": "Connection refused", "host": "web-01"}}\n'
        '{"preview": true}\n'
    )
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.text = ndjson_response

    with patch("app.services.splunk.client.httpx.post", return_value=mock_resp):
        result = client.search_logs(query='index=main "error" | head 10')

    assert result["success"] is True
    assert result["total"] == 2
    assert result["logs"][0]["message"] == "NullPointerException"
    assert result["logs"][0]["host"] == "web-01"


def test_search_logs_http_error_returns_failure() -> None:
    config = SplunkConfig(base_url="https://splunk:8089", token="tok")
    client = SplunkClient(config)
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "Forbidden"
    with patch(
        "app.services.splunk.client.httpx.post",
        side_effect=httpx.HTTPStatusError("", request=MagicMock(), response=mock_response),
    ):
        result = client.search_logs(query="index=main | head 10")
    assert result["success"] is False
    assert "403" in result["error"]


# ── _normalize_row ────────────────────────────────────────────────────────────

def test_normalize_row_extracts_standard_fields() -> None:
    config = SplunkConfig(base_url="https://splunk:8089", token="tok")
    client = SplunkClient(config)
    row = {
        "_time": "2024-01-01T00:00:00Z",
        "_raw": "some log message",
        "host": "web-01",
        "source": "/var/log/app.log",
        "sourcetype": "app_logs",
        "index": "main",
        "log_level": "ERROR",
    }
    normalized = client._normalize_row(row)
    assert normalized["timestamp"] == "2024-01-01T00:00:00Z"
    assert normalized["message"] == "some log message"
    assert normalized["host"] == "web-01"
    assert normalized["level"] == "ERROR"
    assert normalized["raw"] == row


def test_normalize_row_falls_back_message_field() -> None:
    config = SplunkConfig(base_url="https://splunk:8089", token="tok")
    client = SplunkClient(config)
    row = {"message": "fallback message"}
    normalized = client._normalize_row(row)
    assert normalized["message"] == "fallback message"


# ── catalog classification ────────────────────────────────────────────────────

def test_catalog_classifies_splunk_from_store() -> None:
    from app.integrations.catalog import classify_integrations

    integrations = [
        {
            "id": "splunk-1",
            "service": "splunk",
            "status": "active",
            "instances": [
                {
                    "name": "default",
                    "tags": {},
                    "credentials": {
                        "base_url": "https://splunk.corp.com:8089",
                        "token": "test-token",
                        "index": "main",
                        "verify_ssl": True,
                    },
                }
            ],
        }
    ]
    resolved = classify_integrations(integrations)
    assert "splunk" in resolved
    assert resolved["splunk"]["base_url"] == "https://splunk.corp.com:8089"
    assert resolved["splunk"]["index"] == "main"


def test_catalog_classifies_splunk_v1_flat_credentials() -> None:
    from app.integrations.catalog import classify_integrations

    integrations = [
        {
            "id": "splunk-v1",
            "service": "splunk",
            "status": "active",
            "credentials": {
                "base_url": "https://splunk.corp.com:8089",
                "token": "test-token",
                "index": "prod",
            },
        }
    ]
    resolved = classify_integrations(integrations)
    assert "splunk" in resolved
    assert resolved["splunk"]["index"] == "prod"


def test_catalog_ignores_splunk_without_base_url() -> None:
    from app.integrations.catalog import classify_integrations

    integrations = [
        {
            "id": "splunk-bad",
            "service": "splunk",
            "status": "active",
            "instances": [
                {"name": "default", "tags": {}, "credentials": {"base_url": "", "token": "tok"}},
            ],
        }
    ]
    resolved = classify_integrations(integrations)
    assert "splunk" not in resolved


def test_catalog_ignores_splunk_without_token() -> None:
    from app.integrations.catalog import classify_integrations

    integrations = [
        {
            "id": "splunk-no-token",
            "service": "splunk",
            "status": "active",
            "credentials": {
                "base_url": "https://splunk:8089",
                "token": "",
            },
        }
    ]
    resolved = classify_integrations(integrations)
    assert "splunk" not in resolved


# ── env var loading ───────────────────────────────────────────────────────────

def test_env_loader_picks_up_splunk_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.catalog import load_env_integrations

    monkeypatch.setenv("SPLUNK_URL", "https://splunk.test:8089")
    monkeypatch.setenv("SPLUNK_TOKEN", "env-test-token")
    monkeypatch.setenv("SPLUNK_INDEX", "prod")

    integrations = load_env_integrations()
    splunk = next((i for i in integrations if i["service"] == "splunk"), None)
    assert splunk is not None
    creds = splunk.get("credentials", {})
    assert creds["base_url"] == "https://splunk.test:8089"
    assert creds["index"] == "prod"


def test_env_loader_splunk_default_index_is_main(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.catalog import load_env_integrations

    monkeypatch.setenv("SPLUNK_URL", "https://splunk.test:8089")
    monkeypatch.setenv("SPLUNK_TOKEN", "env-test-token")
    monkeypatch.delenv("SPLUNK_INDEX", raising=False)

    integrations = load_env_integrations()
    splunk = next((i for i in integrations if i["service"] == "splunk"), None)
    assert splunk is not None
    assert splunk["credentials"]["index"] == "main"


def test_env_loader_splunk_not_loaded_when_url_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.catalog import load_env_integrations

    monkeypatch.delenv("SPLUNK_URL", raising=False)
    monkeypatch.delenv("SPLUNK_INSTANCES", raising=False)
    monkeypatch.setenv("SPLUNK_TOKEN", "env-test-token")

    integrations = load_env_integrations()
    splunk = next((i for i in integrations if i["service"] == "splunk"), None)
    assert splunk is None


def test_env_loader_splunk_verify_ssl_false(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.catalog import load_env_integrations

    monkeypatch.setenv("SPLUNK_URL", "https://splunk.test:8089")
    monkeypatch.setenv("SPLUNK_TOKEN", "tok")
    monkeypatch.setenv("SPLUNK_VERIFY_SSL", "false")

    integrations = load_env_integrations()
    splunk = next((i for i in integrations if i["service"] == "splunk"), None)
    assert splunk is not None
    assert splunk["credentials"]["verify_ssl"] is False
