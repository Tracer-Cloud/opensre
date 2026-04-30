from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.datadog.client import DatadogClient, DatadogConfig

# -------------------------
# Fixtures
# -------------------------


@pytest.fixture
def config():
    return DatadogConfig(
        api_key="test-api-key",
        app_key="test-app-key",
        site="datadoghq.com",
    )


@pytest.fixture
def client(config):
    return DatadogClient(config)


@pytest.fixture
def mock_httpx_client():
    with patch("app.services.datadog.client.httpx.Client") as mock:
        yield mock


# -------------------------
# search_logs
# -------------------------


def test_search_logs_success(client, mock_httpx_client):
    mock_instance = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"attributes": {"message": "log message"}}]}

    mock_instance.post.return_value = mock_response
    mock_httpx_client.return_value = mock_instance

    result = client.search_logs("error")
    mock_instance.post.assert_called_once()

    mock_response.raise_for_status.assert_called_once()
    assert "logs" in result
    assert result["success"] is True
    assert result["logs"][0]["message"] == "log message"


def test_search_logs_empty_data(client, mock_httpx_client):
    mock_instance = MagicMock()

    mock_instance.post.return_value = MagicMock(
        json=lambda: {"data": []},
        raise_for_status=MagicMock(),
    )

    mock_httpx_client.return_value = mock_instance

    result = client.search_logs("error")

    mock_instance.post.assert_called_once()

    assert result["success"] is True
    assert result["logs"] == []
    assert result["total"] == 0


def test_search_logs_http_error(client, mock_httpx_client):
    mock_instance = MagicMock()
    mock_httpx_client.return_value = mock_instance

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "server error"

    error = httpx.HTTPStatusError(
        "error",
        request=MagicMock(),
        response=mock_response,
    )

    mock_response.raise_for_status.side_effect = error
    mock_instance.post.return_value = mock_response

    result = client.search_logs("error")
    mock_instance.post.assert_called_once()
    assert result["success"] is False
    assert "HTTP 500" in result["error"]


def test_search_logs_generic_exception(client, mock_httpx_client):
    mock_instance = MagicMock()

    mock_instance.post.side_effect = Exception("unexpected error")

    mock_httpx_client.return_value = mock_instance

    result = client.search_logs("error")
    mock_instance.post.assert_called_once()

    assert result["success"] is False
    assert result["error"] == "unexpected error"


# -------------------------
# list_monitors
# -------------------------


def test_list_monitors_success(client, mock_httpx_client):
    mock_instance = MagicMock()

    mock_instance.get.return_value = MagicMock(
        json=lambda: [{"name": "CPU Monitor"}],
        raise_for_status=MagicMock(),
    )

    mock_httpx_client.return_value = mock_instance

    result = client.list_monitors()
    mock_instance.get.assert_called_once()

    assert result["success"] is True
    assert result["monitors"][0]["name"] == "CPU Monitor"


def test_list_monitors_empty(client, mock_httpx_client):
    mock_instance = MagicMock()
    mock_httpx_client.return_value = mock_instance

    mock_response = MagicMock()
    mock_response.json.return_value = []
    mock_response.raise_for_status.return_value = None
    mock_instance.get.return_value = mock_response

    result = client.list_monitors()

    mock_instance.get.assert_called_once()
    assert result["success"] is True
    assert result["monitors"] == []
    assert result["total"] == 0


def test_list_monitors_http_error(client, mock_httpx_client):
    mock_instance = MagicMock()
    mock_httpx_client.return_value = mock_instance

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "forbidden"

    error = httpx.HTTPStatusError(
        "error",
        request=MagicMock(),
        response=mock_response,
    )

    mock_response.raise_for_status.side_effect = error
    mock_instance.get.return_value = mock_response

    result = client.list_monitors()
    mock_instance.get.assert_called_once()
    assert result["success"] is False
    assert "HTTP 403" in result["error"]


def test_list_monitors_generic_exception(client, mock_httpx_client):
    mock_instance = MagicMock()
    mock_httpx_client.return_value = mock_instance

    mock_instance.get.side_effect = Exception("boom")

    result = client.list_monitors()
    mock_instance.get.assert_called_once()

    assert result["success"] is False
    assert result["error"] == "boom"


# -------------------------
# get_events
# -------------------------


def test_get_events_success(client, mock_httpx_client):
    mock_instance = MagicMock()

    mock_instance.post.return_value = MagicMock(
        json=lambda: {"data": [{"attributes": {"title": "event title"}}]},
        raise_for_status=MagicMock(),
    )

    mock_httpx_client.return_value = mock_instance

    result = client.get_events("error")
    mock_instance.post.assert_called_once()

    assert result["success"] is True
    assert result["events"][0]["title"] == "event title"


def test_get_events_empty(client, mock_httpx_client):
    mock_instance = MagicMock()
    mock_httpx_client.return_value = mock_instance

    mock_response = MagicMock()
    mock_response.json.return_value = {"data": []}
    mock_response.raise_for_status.return_value = None
    mock_instance.post.return_value = mock_response

    result = client.get_events()

    mock_instance.post.assert_called_once()
    assert result["success"] is True
    assert result["events"] == []
    assert result["total"] == 0


def test_get_events_http_error(client, mock_httpx_client):
    mock_instance = MagicMock()
    mock_httpx_client.return_value = mock_instance

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "server error"

    error = httpx.HTTPStatusError(
        "error",
        request=MagicMock(),
        response=mock_response,
    )

    mock_response.raise_for_status.side_effect = error
    mock_instance.post.return_value = mock_response

    result = client.get_events("error")

    mock_instance.post.assert_called_once()
    assert result["success"] is False
    assert "HTTP 500" in result["error"]


def test_get_events_generic_exception(client, mock_httpx_client):
    mock_instance = MagicMock()
    mock_httpx_client.return_value = mock_instance

    mock_instance.post.side_effect = Exception("timeout")

    result = client.get_events("error")

    mock_instance.post.assert_called_once()
    assert result["success"] is False
    assert result["error"] == "timeout"


# -------------------------
# is_configured
# -------------------------


def test_is_configured_true():
    client = DatadogClient(DatadogConfig(api_key="a", app_key="b"))
    assert client.is_configured is True


def test_is_configured_false():
    client = DatadogClient(DatadogConfig(api_key="", app_key=""))
    assert client.is_configured is False


def test_is_configured_missing_api_key():
    config = DatadogConfig(api_key="", app_key="key")
    client = DatadogClient(config)

    assert client.is_configured is False


def test_is_configured_missing_app_key():
    config = DatadogConfig(api_key="key", app_key="")
    client = DatadogClient(config)

    assert client.is_configured is False


# -------------------------
# POD NODE
# -------------------------


def test_get_pods_on_node_success(client):
    client.search_logs = MagicMock(
        return_value={
            "success": True,
            "logs": [
                {
                    "tags": [
                        "pod_name:pod-1",
                        "node_ip:10.0.0.1",
                        "exit_code:1",
                    ]
                },
                {
                    "tags": [
                        "pod_name:pod-2",
                        "node_ip:10.0.0.1",
                    ]
                },
                {
                    "tags": [
                        "pod_name:pod-1",
                        "node_ip:10.0.0.1",
                    ]
                },
            ],
        }
    )

    result = client.get_pods_on_node("10.0.0.1")

    assert result["success"] is True
    assert result["total"] == 2

    pod1 = next(p for p in result["pods"] if p["pod_name"] == "pod-1")
    assert pod1["status"] == "failed"
    assert pod1["exit_code"] == "1"

    pod2 = next(p for p in result["pods"] if p["pod_name"] == "pod-2")
    assert pod2["status"] == "running"


def test_get_pods_on_node_failure(client):
    client.search_logs = MagicMock(
        return_value={
            "success": False,
            "error": "datadog failure",
        }
    )

    result = client.get_pods_on_node("10.0.0.1")

    assert result["success"] is False
    assert result["pods"] == []
    assert result["error"] == "datadog failure"


def test_get_pods_on_node_missing_tags(client):
    client.search_logs = MagicMock(
        return_value={
            "success": True,
            "logs": [{}],
        }
    )

    result = client.get_pods_on_node("10.0.0.1")

    assert result["success"] is True
    assert result["pods"] == []
