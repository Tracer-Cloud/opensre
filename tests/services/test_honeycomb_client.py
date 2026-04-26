"""Tests for the Honeycomb service client."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.integrations.models import HoneycombIntegrationConfig
from app.services.honeycomb.client import HoneycombClient


@pytest.fixture
def config() -> HoneycombIntegrationConfig:
    return HoneycombIntegrationConfig(
        api_key="test-api-key",
        dataset="test-dataset",
        base_url="https://api.honeycomb.io",
    )


@pytest.fixture
def client(config: HoneycombIntegrationConfig) -> HoneycombClient:
    return HoneycombClient(config)


def test_is_configured(config: HoneycombIntegrationConfig) -> None:
    # Both present
    client = HoneycombClient(config)
    assert client.is_configured is True

    # Missing API key
    config_no_key = HoneycombIntegrationConfig(api_key="", dataset="ds")
    client_no_key = HoneycombClient(config_no_key)
    assert client_no_key.is_configured is False

    # Missing dataset
    # Note: HoneycombIntegrationConfig has a default for dataset, so we test with empty string
    config_no_ds = HoneycombIntegrationConfig(api_key="key", dataset="")
    client_no_ds = HoneycombClient(config_no_ds)
    # The normalize_dataset validator might prevent empty string, let's check the code
    # Actually it returns DEFAULT_HONEYCOMB_DATASET if stripped value is empty
    # So we should check if is_configured handles that or if we need to force it.
    # But based on the code: bool(self.config.api_key and self.config.dataset)
    # If dataset is "__all__", it's truthy.
    assert client_no_ds.is_configured is True  # because it defaults to "__all__"


def test_validate_access_success(client: HoneycombClient) -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "environment": {"name": "prod"},
        "team": {"name": "my-team"},
        "type": "ingest",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.Client.get", return_value=mock_response) as mock_get:
        result = client.validate_access()
        mock_get.assert_called_once_with("/1/auth")

    assert result["success"] is True
    assert result["environment"] == {"name": "prod"}
    assert result["team"] == {"name": "my-team"}
    assert result["key_type"] == "ingest"


def test_validate_access_http_error(client: HoneycombClient) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    # We need to simulate httpx.HTTPStatusError
    error = httpx.HTTPStatusError("Auth failed", request=MagicMock(), response=mock_response)
    mock_response.raise_for_status.side_effect = error

    with patch("httpx.Client.get", return_value=mock_response):
        result = client.validate_access()

    assert result["success"] is False
    assert "HTTP 401: Unauthorized" in result["error"]


def test_validate_access_generic_exception(client: HoneycombClient) -> None:
    with patch("httpx.Client.get", side_effect=Exception("Connection refused")):
        result = client.validate_access()

    assert result["success"] is False
    assert "Connection refused" in result["error"]


def test_create_query_success(client: HoneycombClient) -> None:
    query_spec = {"calculations": [{"op": "COUNT"}]}
    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "query-123", "calculations": []}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.Client.post", return_value=mock_response) as mock_post:
        result = client.create_query(query_spec)
        mock_post.assert_called_once_with("/1/queries/test-dataset", json=query_spec)

    assert result["success"] is True
    assert result["query_id"] == "query-123"
    assert result["query"] == {"id": "query-123", "calculations": []}


def test_create_query_no_id(client: HoneycombClient) -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = {"something": "else"}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.Client.post", return_value=mock_response):
        result = client.create_query({})

    assert result["success"] is False
    assert "returned no query ID" in result["error"]


def test_create_query_http_error(client: HoneycombClient) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Bad Request"
    error = httpx.HTTPStatusError("Err", request=MagicMock(), response=mock_response)
    mock_response.raise_for_status.side_effect = error

    with patch("httpx.Client.post", return_value=mock_response):
        result = client.create_query({})

    assert result["success"] is False
    assert "HTTP 400: Bad Request" in result["error"]


def test_create_query_result_success(client: HoneycombClient) -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "result-456", "complete": False}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.Client.post", return_value=mock_response) as mock_post:
        result = client.create_query_result("query-123", limit=10)
        # Check payload
        args, kwargs = mock_post.call_args
        assert args[0] == "/1/query_results/test-dataset"
        assert kwargs["json"]["query_id"] == "query-123"
        assert kwargs["json"]["limit"] == 10

    assert result["success"] is True
    assert result["result"]["id"] == "result-456"


def test_create_query_result_http_error(client: HoneycombClient) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    error = httpx.HTTPStatusError("Err", request=MagicMock(), response=mock_response)
    mock_response.raise_for_status.side_effect = error

    with patch("httpx.Client.post", return_value=mock_response):
        result = client.create_query_result("q", limit=1)

    assert result["success"] is False
    assert "HTTP 500: Internal Server Error" in result["error"]


def test_get_query_result_success(client: HoneycombClient) -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "id": "result-456",
        "complete": True,
        "data": {"results": []},
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.Client.get", return_value=mock_response) as mock_get:
        result = client.get_query_result("result-456")
        mock_get.assert_called_once_with("/1/query_results/test-dataset/result-456")

    assert result["success"] is True
    assert result["result"]["complete"] is True


def test_get_query_result_http_error(client: HoneycombClient) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = "Not Found"
    error = httpx.HTTPStatusError("Err", request=MagicMock(), response=mock_response)
    mock_response.raise_for_status.side_effect = error

    with patch("httpx.Client.get", return_value=mock_response):
        result = client.get_query_result("missing")

    assert result["success"] is False
    assert "HTTP 404: Not Found" in result["error"]


def test_get_query_result_generic_exception(client: HoneycombClient) -> None:
    with patch("httpx.Client.get", side_effect=Exception("Timeout")):
        result = client.get_query_result("result-id")

    assert result["success"] is False
    assert "Timeout" in result["error"]


def test_run_query_full_flow(client: HoneycombClient) -> None:
    # We'll mock the internal methods to avoid deep httpx patching
    client.create_query = MagicMock(return_value={"success": True, "query_id": "q1"})
    client.create_query_result = MagicMock(
        return_value={
            "success": True,
            "result": {"id": "r1", "complete": True, "data": {"results": [{"data": {"val": 10}}]}},
        }
    )

    # We also need to mock time.sleep to speed up tests if it ever hits it
    with patch("time.sleep"):
        result = client.run_query({"calculations": []})

    assert result["success"] is True
    assert result["query_id"] == "q1"
    assert result["query_result_id"] == "r1"
    assert result["results"] == [{"val": 10}]


def test_query_traces_success(client: HoneycombClient) -> None:
    client.run_query = MagicMock(return_value={"success": True, "results": []})

    result = client.query_traces(service_name="auth-service", trace_id="trace-abc")

    assert result["success"] is True
    # Verify run_query was called with correct filter shape
    args, kwargs = client.run_query.call_args
    query = args[0]
    filters = query["filters"]
    assert any(f["column"] == "service.name" and f["value"] == "auth-service" for f in filters)
    assert any(f["column"] == "trace.trace_id" and f["value"] == "trace-abc" for f in filters)
