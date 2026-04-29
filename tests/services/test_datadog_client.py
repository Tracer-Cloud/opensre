from unittest.mock import AsyncMock, MagicMock, patch

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

    async def post_router(*args, **kwargs):
        return MagicMock(
            json=lambda: {
                "data": [
                    {
                        "attributes": {
                            "message": "log message",
                        }
                    }
                ]
            },
            raise_for_status=lambda: None,
        )

    async def get_router(*args, **kwargs):
        return MagicMock(json=lambda: [{"name": "CPU Monitor"}], raise_for_status=lambda: None)

    mock_instance.post = AsyncMock(side_effect=post_router)
    mock_instance.get = AsyncMock(side_effect=get_router)
    mock_httpx_client.return_value = mock_instance

    result = client.search_logs("error")

    assert "logs" in result
    assert "monitors" in result
    assert "events" in result

    # REQUIRED per review
    assert result["logs"]["success"] is True
    assert result["monitors"]["success"] is True
    assert result["events"]["success"] is True

    assert result["logs"]["logs"][0]["message"] == "log message"
    assert result["monitors"]["monitors"][0]["name"] == "CPU Monitor"


def test_search_logs_empty_data(client, mock_httpx_client):
    mock_instance = MagicMock()

    async def post_router(*args, **kwargs):
        return MagicMock(json=lambda: {"data": []}, raise_for_status=lambda: None)

    async def get_router(*args, **kwargs):
        return MagicMock(json=lambda: [], raise_for_status=lambda: None)

    mock_instance.post = AsyncMock(side_effect=post_router)
    mock_instance.get = AsyncMock(side_effect=get_router)
    mock_httpx_client.return_value = mock_instance

    result = client.search_logs("error")

    assert result["logs"]["success"] is True
    assert result["logs"]["logs"] == []

    assert result["monitors"]["success"] is True
    assert result["events"]["success"] is True


def test_search_logs_http_error(client, mock_httpx_client):
    mock_instance = MagicMock()

    async def post_router(*args, **kwargs):
        raise httpx.HTTPStatusError(
            "error",
            request=MagicMock(),
            response=MagicMock(status_code=500, text="server error"),
        )

    mock_instance.post = AsyncMock(side_effect=post_router)
    mock_instance.get = AsyncMock()
    mock_httpx_client.return_value = mock_instance

    result = client.search_logs("error")

    assert result["logs"]["success"] is False
    assert "HTTP 500" in result["logs"]["error"]


def test_search_logs_generic_exception(client, mock_httpx_client):
    mock_instance = MagicMock()

    mock_instance.post = AsyncMock(side_effect=Exception("unexpected error"))
    mock_instance.get = AsyncMock()
    mock_httpx_client.return_value = mock_instance

    result = client.search_logs("error")

    assert result["success"] is False
    assert result["error"] == "unexpected error"


# -------------------------
# list_monitors
# -------------------------


def test_list_monitors_success(client, mock_httpx_client):
    mock_instance = MagicMock()

    async def get_router(*args, **kwargs):
        return MagicMock(json=lambda: [{"name": "CPU Monitor"}], raise_for_status=lambda: None)

    mock_instance.get = AsyncMock(side_effect=get_router)
    mock_instance.post = AsyncMock()
    mock_httpx_client.return_value = mock_instance

    result = client.list_monitors()

    assert result["success"] is True
    assert result["monitors"][0]["name"] == "CPU Monitor"


# -------------------------
# get_events
# -------------------------


def test_get_events_success(client, mock_httpx_client):
    mock_instance = MagicMock()

    async def post_router(*args, **kwargs):
        return MagicMock(
            json=lambda: {"data": [{"attributes": {"title": "event title"}}]},
            raise_for_status=lambda: None,
        )

    mock_instance.post = AsyncMock(side_effect=post_router)
    mock_instance.get = AsyncMock()
    mock_httpx_client.return_value = mock_instance

    result = client.get_events("error")

    assert result["success"] is True
    assert result["events"][0]["title"] == "event title"


# -------------------------
# is_configured
# -------------------------


def test_is_configured_true():
    client = DatadogClient(DatadogConfig(api_key="a", app_key="b"))
    assert client.is_configured is True


def test_is_configured_false():
    client = DatadogClient(DatadogConfig(api_key="", app_key=""))
    assert client.is_configured is False


# -------------------------
# POD NODE (FIXED PROPERLY)
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

    pods = result["pods"]

    pod1 = next(p for p in pods if p["pod_name"] == "pod-1")
    assert pod1["status"] == "failed"
    assert pod1["exit_code"] == "1"

    pod2 = next(p for p in pods if p["pod_name"] == "pod-2")
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
