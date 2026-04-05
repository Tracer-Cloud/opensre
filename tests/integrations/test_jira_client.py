"""Tests for the Jira integration client."""

from unittest.mock import MagicMock, patch

import pytest

from app.integrations.models import JiraIntegrationConfig as JiraConfig
from app.services.jira.client import JiraClient


@pytest.fixture
def config() -> JiraConfig:
    return JiraConfig(
        base_url="https://myteam.atlassian.net",
        email="user@example.com",
        api_token="test-token-123",
        project_key="OPS",
    )


@pytest.fixture
def client(config: JiraConfig) -> JiraClient:
    return JiraClient(config)


def test_is_configured(client: JiraClient) -> None:
    assert client.is_configured is True


def test_is_not_configured_missing_token() -> None:
    config = JiraConfig(
        base_url="https://myteam.atlassian.net",
        email="user@example.com",
        api_token="",
        project_key="OPS",
    )
    assert JiraClient(config).is_configured is False


def test_create_issue_success(client: JiraClient) -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"key": "OPS-42", "id": "10042"}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client.post", return_value=mock_resp):
        result = client.create_issue(
            summary="DB connection spike",
            description="Connection pool exhausted during peak traffic.",
            priority="High",
            labels=["incident", "rca"],
        )

    assert result["success"] is True
    assert result["issue_key"] == "OPS-42"
    assert "browse/OPS-42" in result["url"]


def test_create_issue_http_error(client: JiraClient) -> None:
    import httpx

    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Forbidden"

    with patch(
        "httpx.Client.post",
        side_effect=httpx.HTTPStatusError("err", request=MagicMock(), response=mock_resp),
    ):
        result = client.create_issue(summary="test", description="test")

    assert result["success"] is False
    assert "403" in result["error"]


def test_add_comment_success(client: JiraClient) -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": "comment-99"}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client.post", return_value=mock_resp):
        result = client.add_comment("OPS-42", "RCA complete. Root cause: pool exhaustion.")

    assert result["success"] is True
    assert result["comment_id"] == "comment-99"


def test_get_issue_success(client: JiraClient) -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "key": "OPS-42",
        "fields": {
            "summary": "DB spike",
            "status": {"name": "Open"},
            "priority": {"name": "High"},
            "description": "some desc",
            "labels": ["incident"],
        },
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client.get", return_value=mock_resp):
        result = client.get_issue("OPS-42")

    assert result["success"] is True
    assert result["issue_key"] == "OPS-42"
    assert result["status"] == "Open"

def test_update_issue_success(client: JiraClient) -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client.put", return_value=mock_resp):
        result = client.update_issue("OPS-42", {"priority": {"name": "Low"}})

    assert result["success"] is True
    assert result["issue_key"] == "OPS-42"


def test_update_issue_http_error(client: JiraClient) -> None:
    import httpx

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = "Bad Request"

    with patch(
        "httpx.Client.put",
        side_effect=httpx.HTTPStatusError("err", request=MagicMock(), response=mock_resp),
    ):
        result = client.update_issue("OPS-42", {"priority": {"name": "Low"}})

    assert result["success"] is False
    assert "400" in result["error"]
