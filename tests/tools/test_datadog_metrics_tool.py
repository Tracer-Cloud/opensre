"""Tests for DataDogMetricsTool (function-based stub, @tool decorated)."""

from __future__ import annotations

from app.tools.DataDogMetricsTool import query_datadog_metrics
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestDataDogMetricsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return query_datadog_metrics.__opensre_registered_tool__


def test_is_available_requires_connection_verified() -> None:
    rt = query_datadog_metrics.__opensre_registered_tool__
    assert rt.is_available({"datadog": {"connection_verified": True}}) is True
    assert rt.is_available({"datadog": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = query_datadog_metrics.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert "metric_name" in params
    assert params["api_key"] == "dd_api_key_test"


def test_run_returns_stub_unavailable() -> None:
    # This tool is a stub
    result = query_datadog_metrics(metric_name="system.cpu.user")
    assert result["available"] is False
    assert result["metric_name"] == "system.cpu.user"
    assert result["metrics"] == []


def test_run_metadata() -> None:
    rt = query_datadog_metrics.__opensre_registered_tool__
    assert rt.name == "query_datadog_metrics"
    assert rt.source == "datadog"
