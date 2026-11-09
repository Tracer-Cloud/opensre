"""Direct tests for CoralogixClient HTTP envelope, NDJSON parsing, and probes."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from integrations.config_models import CoralogixIntegrationConfig
from integrations.coralogix.client import (
    CoralogixClient,
    build_coralogix_logs_query,
)

# -------------------------
# Fixtures
# -------------------------


@pytest.fixture
def config() -> CoralogixIntegrationConfig:
    return CoralogixIntegrationConfig(
        api_key="test-key",
        base_url="https://api.eu2.coralogix.com",
        application_name="opensre",
        subsystem_name="api",
    )


@pytest.fixture
def client(config: CoralogixIntegrationConfig) -> CoralogixClient:
    return CoralogixClient(config)


def _make_ndjson_success_line() -> str:
    return json.dumps(
        {
            "result": {
                "results": [
                    {
                        "metadata": [
                            {"key": "timestamp", "value": "2026-05-04T00:00:00Z"},
                        ],
                        "labels": [
                            {"key": "applicationname", "value": "opensre"},
                            {"key": "subsystemname", "value": "api"},
                        ],
                        "userData": json.dumps(
                            {
                                "log_obj": {
                                    "message": "boom",
                                    "level": "ERROR",
                                    "timestamp": "2026-05-04T00:00:00Z",
                                },
                                "trace_id": "abc-123",
                            }
                        ),
                    }
                ]
            }
        }
    )


# -------------------------
# query_logs
# -------------------------


def test_query_logs_success_parses_ndjson_rows(client: CoralogixClient) -> None:
    mock_response = MagicMock(text=_make_ndjson_success_line())
    mock_response.raise_for_status = MagicMock()

    with patch("integrations.coralogix.client.httpx.post", return_value=mock_response) as mock_post:
        result = client.query_logs("source logs")
        mock_post.assert_called_once()

    assert result["success"] is True
    assert result["total"] == 1
    assert result["logs"][0]["message"] == "boom"
    assert result["logs"][0]["level"] == "ERROR"
    assert result["logs"][0]["trace_id"] == "abc-123"
    assert result["logs"][0]["application_name"] == "opensre"
    assert result["logs"][0]["subsystem_name"] == "api"


def test_query_logs_http_error_returns_failure_envelope(client: CoralogixClient) -> None:
    mock_response = MagicMock(status_code=500, text="server error")
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "error",
        request=MagicMock(),
        response=mock_response,
    )

    with patch("integrations.coralogix.client.httpx.post", return_value=mock_response):
        result = client.query_logs("source logs")

    assert result["success"] is False
    assert "HTTP 500" in result["error"]


def test_query_logs_empty_response_returns_zero_logs(client: CoralogixClient) -> None:
    mock_response = MagicMock(text="")
    mock_response.raise_for_status = MagicMock()

    with patch("integrations.coralogix.client.httpx.post", return_value=mock_response):
        result = client.query_logs("source logs")

    assert result["success"] is True
    assert result["total"] == 0
    assert result["logs"] == []


def test_query_logs_invalid_json_lines_silently_skipped(client: CoralogixClient) -> None:
    mock_response = MagicMock(text="not json\n{bad json\n")
    mock_response.raise_for_status = MagicMock()

    with patch("integrations.coralogix.client.httpx.post", return_value=mock_response):
        result = client.query_logs("source logs")

    assert result["success"] is True
    assert result["total"] == 0
    assert result["logs"] == []


def test_query_logs_network_exception_returns_failure_envelope(
    client: CoralogixClient,
) -> None:
    with patch(
        "integrations.coralogix.client.httpx.post",
        side_effect=httpx.RequestError("connection refused"),
    ):
        result = client.query_logs("source logs")

    assert result["success"] is False
    assert "connection refused" in result["error"]


# -------------------------
# validate_access
# -------------------------


def test_validate_access_passes_through_total_and_warnings(client: CoralogixClient) -> None:
    with patch.object(
        client,
        "query_logs",
        return_value={
            "success": True,
            "total": 3,
            "warnings": ["heads up"],
            "logs": [],
        },
    ):
        result = client.validate_access()

    assert result["success"] is True
    assert result["total"] == 3
    assert result["warnings"] == ["heads up"]


# -------------------------
# probe_access
# -------------------------


def test_probe_access_missing_when_unconfigured() -> None:
    bare_client = CoralogixClient(
        CoralogixIntegrationConfig(api_key="", base_url="https://example.com")
    )

    probe = bare_client.probe_access()

    assert probe.status == "missing"
    assert "Missing Coralogix" in probe.detail


def test_probe_access_failed_when_query_fails(client: CoralogixClient) -> None:
    with patch.object(
        client,
        "validate_access",
        return_value={"success": False, "error": "boom"},
    ):
        probe = client.probe_access()

    assert probe.status == "failed"
    assert "DataPrime check failed" in probe.detail
    assert "boom" in probe.detail


def test_probe_access_passed_with_scope_in_message(client: CoralogixClient) -> None:
    with patch.object(
        client,
        "validate_access",
        return_value={"success": True, "total": 7, "warnings": []},
    ):
        probe = client.probe_access()

    assert probe.status == "passed"
    assert "opensre" in probe.detail
    assert "api" in probe.detail
    assert "7 row" in probe.detail


# -------------------------
# build_coralogix_logs_query (pure helper)
# -------------------------


def test_build_coralogix_logs_query_appends_limit_clause() -> None:
    result = build_coralogix_logs_query(application_name="opensre", limit=5)

    assert result.startswith("source logs")
    assert "filter $l.applicationname == 'opensre'" in result
    assert result.endswith("limit 5")


def test_build_coralogix_logs_query_raw_query_passthrough_appends_limit() -> None:
    result = build_coralogix_logs_query(
        raw_query="source logs | filter foo == 'bar'",
        application_name="ignored",
        subsystem_name="ignored",
        limit=10,
    )

    assert result == "source logs | filter foo == 'bar' | limit 10"
    assert "applicationname" not in result
    assert "subsystemname" not in result


def test_build_coralogix_logs_query_subsystem_name_filter() -> None:
    result = build_coralogix_logs_query(subsystem_name="api-gateway", limit=5)

    assert result.startswith("source logs")
    assert "filter $l.subsystemname == 'api-gateway'" in result
    assert result.endswith("limit 5")


def test_parse_user_data_counts_successes_and_failures_symmetrically(
    client: CoralogixClient,
) -> None:
    """Both branches of _parse_user_data must update the stats denominator.

    Without record_parsed on success the skip ratio would only ever rise:
    100 good userData blobs + 5 broken ones would compute as 5/5 = 100% skip
    instead of 5/105 = ~5%, firing a false alert on every response.
    """
    from infrastructure.observability.streaming import StreamingParseStats

    stats = StreamingParseStats()
    # A JSON-string userData that parses cleanly.
    client._parse_user_data(json.dumps({"k": "v"}), stats=stats)
    client._parse_user_data(json.dumps({"k": "v"}), stats=stats)
    # A broken userData blob.
    client._parse_user_data("{not json", stats=stats)
    assert stats.parsed == 2
    assert stats.skipped == 1
    # dict / empty-string branches are not parse attempts and must not move
    # either counter.
    client._parse_user_data({"already": "a dict"}, stats=stats)
    client._parse_user_data("", stats=stats)
    client._parse_user_data(None, stats=stats)
    assert stats.parsed == 2
    assert stats.skipped == 1
