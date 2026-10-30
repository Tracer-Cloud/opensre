"""Dynatrace logs tool contract and behavior tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from integrations.dynatrace.tools.query_dynatrace_logs_tool.query import build_logs_query
from integrations.dynatrace.tools.query_dynatrace_logs_tool.tool import (
    DynatraceLogsTool,
    _map_query_dynatrace_logs,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestDynatraceLogsToolContract(BaseToolContract):
    def get_tool_under_test(self) -> DynatraceLogsTool:
        return DynatraceLogsTool()


def test_availability_and_param_injection_require_verified_credentials() -> None:
    tool = DynatraceLogsTool()
    assert tool.is_available({}) is False
    sources = mock_agent_state(
        {
            "dynatrace": {
                "connection_verified": True,
                "tenant_url": "https://abc12345.apps.dynatrace.com",
                "platform_token": "fake-token",
                "integration_id": "dynatrace-prod",
            }
        }
    )
    assert tool.is_available(sources) is True
    assert tool.extract_params(sources) == {
        "tenant_url": "https://abc12345.apps.dynatrace.com",
        "platform_token": "fake-token",
        "integration_id": "dynatrace-prod",
    }


def test_query_builder_uses_kubernetes_fields_and_bounds_cost() -> None:
    dql, timeframe, limit = build_logs_query(
        deployment_name="omnenest-*",
        namespace_name="payments",
        container_name="omnenest",
        service_name="",
        log_level="ERROR",
        search_text='failed"\n| limit 9999',
        timeframe_minutes=99_999,
        limit=99_999,
    )

    assert "from: -1440m" in dql
    assert "scanLimitGBytes: 1" in dql
    assert 'matchesValue(k8s.deployment.name, "omnenest-*")' in dql
    assert "\n| limit 9999\n" not in dql
    assert dql.endswith("| limit 200")
    assert timeframe == 1_440
    assert limit == 200


def test_run_returns_records_and_generated_query() -> None:
    tool = DynatraceLogsTool()
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query.return_value = {
        "success": True,
        "records": [{"content": "failed", "k8s.deployment.name": "omnenest-api"}],
        "metadata": {"grail": {"scannedRecords": 42}},
    }
    with patch(
        "integrations.dynatrace.tools.query_dynatrace_logs_tool.tool.DynatraceClient",
        return_value=mock_client,
    ):
        result = tool.run(
            deployment_name="omnenest-*",
            tenant_url="https://abc12345.apps.dynatrace.com",
            platform_token="fake-token",
            limit=50,
        )

    assert result["available"] is True
    assert result["total"] == 1
    assert result["truncated"] is False
    assert "k8s.deployment.name" in result["query"]
    mock_client.query.assert_called_once()


def test_run_preserves_structured_api_error() -> None:
    tool = DynatraceLogsTool()
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query.return_value = {
        "success": False,
        "error": "Dynatrace API returned HTTP 429.",
        "error_type": "rate_limited",
    }
    with patch(
        "integrations.dynatrace.tools.query_dynatrace_logs_tool.tool.DynatraceClient",
        return_value=mock_client,
    ):
        result = tool.run(
            tenant_url="https://abc12345.apps.dynatrace.com",
            platform_token="fake-token",
        )

    assert result["available"] is False
    assert result["error_type"] == "rate_limited"


def test_evidence_mapper_records_non_empty_results() -> None:
    evidence: dict[str, Any] = {}
    _map_query_dynatrace_logs(
        evidence,
        {"available": True, "records": [{"content": "one"}], "truncated": False},
        {},
    )
    assert evidence["catalog_entries"][0]["source"] == "query_dynatrace_logs"
