"""Unit tests for Grafana Mimir metrics query mixin."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.grafana.mimir import MimirMixin


class DummyMimirClient(MimirMixin):
    """Dummy client to isolate and test the MimirMixin without network calls."""

    def __init__(self, is_configured: bool = True) -> None:
        super().__init__()

        # Fake properties that MimirMixin expects from GrafanaClientBase
        self.is_configured = is_configured
        self.account_id = "test_acc_123"
        self.mimir_datasource_uid = "mimir_uid_456"

        # Mock the internal base methods to intercept network calls
        self._build_datasource_url = MagicMock(return_value="https://fake-grafana.com/api/v1/query")
        self._make_request = MagicMock()


def test_query_mimir_plain_metric():
    """Test query construction for a plain metric without a service filter."""
    client = DummyMimirClient()
    client._make_request.return_value = {"data": {"result": []}}

    client.query_mimir("cpu_usage_total")

    # Verify PromQL exact match (Requirement: Mimir query construction is protected)
    client._make_request.assert_called_once_with(
        "https://fake-grafana.com/api/v1/query", params={"query": "cpu_usage_total"}
    )


def test_query_mimir_service_filtered():
    """Test query construction when a service_name filter is provided."""
    client = DummyMimirClient()
    client._make_request.return_value = {"data": {"result": []}}

    client.query_mimir("cpu_usage_total", service_name="backend-api")

    # Verify PromQL bracket injection (Requirement: Service filtering is tested)
    client._make_request.assert_called_once_with(
        "https://fake-grafana.com/api/v1/query",
        params={"query": 'cpu_usage_total{service_name="backend-api"}'},
    )


def test_query_mimir_result_normalization():
    """Test that raw Grafana JSON is normalized into the expected clean dictionary."""
    client = DummyMimirClient()

    # Simulate a messy response from the real Grafana API
    fake_api_response = {
        "data": {
            "result": [
                {
                    "metric": {"__name__": "cpu_usage_total", "instance": "server-1"},
                    "value": [1670000000, "95.5"],
                }
            ]
        }
    }
    client._make_request.return_value = fake_api_response

    result = client.query_mimir("cpu_usage_total")

    # Requirement: Result-series normalization is covered
    assert result["success"] is True
    assert result["total_series"] == 1
    assert result["query"] == "cpu_usage_total"
    assert len(result["metrics"]) == 1
    assert result["account_id"] == "test_acc_123"

    metric_data = result["metrics"][0]
    assert metric_data["metric"]["instance"] == "server-1"
    assert metric_data["value"][1] == "95.5"


def test_query_mimir_not_configured():
    """Test the early exit path when the client is not configured."""
    client = DummyMimirClient(is_configured=False)

    result = client.query_mimir("cpu_usage_total")

    # Requirement: Not-configured cases
    assert result["success"] is False
    assert "not configured" in result["error"]
    client._make_request.assert_not_called()


def test_query_mimir_exception_handling():
    """Test that network exceptions are caught and wrapped in a safe error envelope."""
    client = DummyMimirClient()

    client._make_request.side_effect = Exception("Network timeout")

    result = client.query_mimir("cpu_usage_total")

    # Requirement: Exception cases / error envelope
    assert result["success"] is False
    assert "Network timeout" in result["error"]
    assert result["metrics"] == []


def test_query_mimir_http_exception_handling():
    client = DummyMimirClient()

    # 1. Create a fake HTTP response object
    mock_response = MagicMock()
    mock_response.status_code = 502
    mock_response.text = "Bad Gateway: Mimir database is unreachable"

    # 2. Create a generic exception, but attach our fake response to it
    mock_exception = Exception("HTTP Error")
    mock_exception.response = mock_response

    # 3. Force the mock client to crash using our custom exception
    client._make_request.side_effect = mock_exception

    result = client.query_mimir("cpu_usage_total")

    # 4. Verify it hit lines 69-71 and correctly formatted the error
    assert result["success"] is False
    assert result["error"] == "Mimir query failed: 502"
    assert result["response"] == "Bad Gateway: Mimir database is unreachable"
    assert result["metrics"] == []
