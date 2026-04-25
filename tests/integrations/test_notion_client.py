"""Tests for the Notion integration client."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.notion.client import NotionClient, NotionConfig


@pytest.fixture
def config() -> NotionConfig:
    return NotionConfig(api_key="secret_test123", database_id="db-abc-123")


@pytest.fixture
def client(config: NotionConfig) -> NotionClient:
    return NotionClient(config)


def test_is_configured(client: NotionClient) -> None:
    assert client.is_configured is True


def test_is_not_configured_missing_key() -> None:
    config = NotionConfig(api_key="", database_id="db-abc")
    client = NotionClient(config)
    assert client.is_configured is False


def test_create_investigation_page_success(client: NotionClient) -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": "page-123", "url": "https://notion.so/page-123"}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client.post", return_value=mock_resp):
        result = client.create_investigation_page(
            title="DB connection spike",
            root_cause="Connection pool exhausted",
            evidence="p99 latency > 5s",
            timeline="14:00 alert fired, 14:05 RCA started",
            suggested_actions="Increase pool size, add circuit breaker",
            severity="high",
        )

    assert result["success"] is True
    assert result["page_id"] == "page-123"


def test_create_investigation_page_http_error(client: NotionClient) -> None:
    import httpx

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"

    with patch(
        "httpx.Client.post",
        side_effect=httpx.HTTPStatusError("err", request=MagicMock(), response=mock_resp),
    ):
        result = client.create_investigation_page(
            title="test",
            root_cause="x",
            evidence="x",
            timeline="x",
            suggested_actions="x",
        )

    assert result["success"] is False
    assert "401" in result["error"]


def test_notion_compatibility_import() -> None:
    """Verify that the old import path still works through __init__."""
    from app.integrations.clients.notion import NotionClient as LegacyClient
    from app.integrations.clients.notion import NotionConfig as LegacyConfig
    from app.services.notion.client import NotionClient, NotionConfig

    assert LegacyClient is NotionClient
    assert LegacyConfig is NotionConfig
