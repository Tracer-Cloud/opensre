from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.datadog.client import DatadogAsyncClient, DatadogConfig

# -------------------------
# fixtures
# -------------------------


@pytest.fixture
def config():
    return DatadogConfig(
        api_key="test-api-key",
        app_key="test-app-key",
        site="datadoghq.com",
    )


@pytest.fixture
def async_client(config):
    return DatadogAsyncClient(config)


@pytest.fixture
def mock_async_httpx():
    with patch("app.services.datadog.client.httpx.AsyncClient") as mock:
        yield mock


# -------------------------
# success test
# -------------------------


@pytest.mark.asyncio
async def test_fetch_all_success_strong(async_client, mock_async_httpx):
    mock_instance = MagicMock()
    mock_async_httpx.return_value.__aenter__.return_value = mock_instance

    # -------- logs --------
    log_response = MagicMock()
    log_response.json.return_value = {"data": [{"attributes": {"message": "log message"}}]}
    log_response.raise_for_status.return_value = None

    # -------- monitors --------
    monitor_response = MagicMock()
    monitor_response.json.return_value = [{"id": 1, "name": "CPU Monitor"}]
    monitor_response.raise_for_status.return_value = None

    # -------- events --------
    event_response = MagicMock()
    event_response.json.return_value = {"data": [{"attributes": {"title": "event title"}}]}
    event_response.raise_for_status.return_value = None

    async def post_router(*args, **kwargs):
        url = str(args[0])

        if "logs" in url:
            return log_response
        if "events" in url:
            return event_response

        raise AssertionError(f"Unexpected POST url: {url}")

    mock_instance.post = AsyncMock(side_effect=post_router)
    mock_instance.get = AsyncMock(return_value=monitor_response)

    result = await async_client.fetch_all(
        logs_query="error",
        time_range_minutes=15,
        logs_limit=100,
        monitor_query="error",
        events_query="error",
    )

    # -------------------------
    # structure
    # -------------------------
    assert "logs" in result
    assert "monitors" in result
    assert "events" in result

    # -------------------------
    # REQUIRED: success per leg
    # -------------------------
    assert result["logs"]["success"] is True
    assert result["monitors"]["success"] is True
    assert result["events"]["success"] is True

    # -------------------------
    # REQUIRED: field validation
    # -------------------------

    assert result["logs"]["logs"][0]["message"] == "log message"
    assert result["monitors"]["monitors"][0]["name"] == "CPU Monitor"
    assert result["events"]["events"][0]["title"] == "event title"

    # -------------------------
    # call validation
    # -------------------------
    assert mock_instance.post.call_count == 2
    assert mock_instance.get.call_count == 1


# -------------------------
# partial failure
# -------------------------


@pytest.mark.asyncio
async def test_fetch_all_partial_failure(async_client, mock_async_httpx):
    mock_instance = MagicMock()
    mock_async_httpx.return_value.__aenter__.return_value = mock_instance

    mock_instance.post = AsyncMock(side_effect=Exception("boom"))
    mock_instance.get = AsyncMock(side_effect=Exception("boom"))

    result = await async_client.fetch_all(
        logs_query="error",
        time_range_minutes=15,
        logs_limit=100,
        monitor_query="error",
        events_query="error",
    )

    assert result["logs"]["success"] is False
    assert result["monitors"]["success"] is False
    assert result["events"]["success"] is False

    assert result["logs"]["error"] is not None
    assert result["monitors"]["error"] is not None
    assert result["events"]["error"] is not None


@pytest.mark.asyncio
async def test_fetch_all_http_error(async_client, mock_async_httpx):
    mock_instance = MagicMock()
    mock_async_httpx.return_value.__aenter__.return_value = mock_instance

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "unauthorized"

    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "err",
            request=MagicMock(),
            response=mock_response,
        )
    )

    mock_instance.post = AsyncMock(return_value=mock_response)
    mock_instance.get = AsyncMock(return_value=mock_response)

    result = await async_client.fetch_all(
        logs_query="error",
        time_range_minutes=15,
        logs_limit=100,
        monitor_query="error",
        events_query="error",
    )

    # logs
    assert result["logs"]["success"] is False
    assert "HTTP 401" in result["logs"]["error"]

    # monitors
    assert result["monitors"]["success"] is False
    assert "HTTP 401" in result["monitors"]["error"]

    # events
    assert result["events"]["success"] is False
    assert "HTTP 401" in result["events"]["error"]


# -------------------------
# is_configured
# -------------------------


def test_async_is_configured_true(config):
    client = DatadogAsyncClient(config)
    assert client.is_configured is True


def test_async_is_configured_false():
    client = DatadogAsyncClient(DatadogConfig(api_key="", app_key=""))
    assert client.is_configured is False
