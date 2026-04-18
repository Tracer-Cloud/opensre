"""Tests for JiraClient.search_issues() and make_jira_client() factory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.integrations.models import JiraIntegrationConfig as JiraConfig
from app.services.jira.client import JiraClient, make_jira_client


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


def test_search_issues_success(client: JiraClient) -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "issues": [
            {
                "key": "OPS-10",
                "fields": {
                    "summary": "API latency spike",
                    "status": {"name": "Open"},
                    "priority": {"name": "High"},
                    "labels": ["incident"],
                    "assignee": {"displayName": "Alice"},
                    "created": "2026-04-01T10:00:00.000+0000",
                    "updated": "2026-04-02T12:00:00.000+0000",
                },
            },
            {
                "key": "OPS-11",
                "fields": {
                    "summary": "DB connection pool exhausted",
                    "status": {"name": "In Progress"},
                    "priority": {"name": "Highest"},
                    "labels": [],
                    "assignee": None,
                    "created": "2026-04-02T08:00:00.000+0000",
                    "updated": "2026-04-02T14:00:00.000+0000",
                },
            },
        ],
        "total": 2,
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client.post", return_value=mock_resp):
        result = client.search_issues(jql="project = OPS", max_results=10)

    assert result["success"] is True
    assert len(result["issues"]) == 2
    assert result["issues"][0]["issue_key"] == "OPS-10"
    assert result["issues"][0]["assignee"] == "Alice"
    assert result["issues"][1]["assignee"] == ""
    assert result["total"] == 2


def test_search_issues_http_error(client: JiraClient) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = "Bad JQL query"

    with patch(
        "httpx.Client.post",
        side_effect=httpx.HTTPStatusError("err", request=MagicMock(), response=mock_resp),
    ):
        result = client.search_issues(jql="invalid jql")

    assert result["success"] is False
    assert "400" in result["error"]


def test_search_issues_defaults_to_project_jql(client: JiraClient) -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"issues": [], "total": 0}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        client.search_issues()

    call_kwargs = mock_post.call_args
    body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert "project = OPS" in body["jql"]


def test_make_jira_client_returns_client() -> None:
    client = make_jira_client(
        base_url="https://myteam.atlassian.net",
        email="user@example.com",
        api_token="token",
        project_key="OPS",
    )
    assert client is not None
    assert isinstance(client, JiraClient)


def test_make_jira_client_returns_none_missing_url() -> None:
    assert make_jira_client("", "user@example.com", "token") is None


def test_make_jira_client_returns_none_missing_email() -> None:
    assert make_jira_client("https://x.atlassian.net", "", "token") is None


def test_make_jira_client_returns_none_missing_token() -> None:
    assert make_jira_client("https://x.atlassian.net", "user@example.com", "") is None


def test_make_jira_client_returns_none_all_none() -> None:
    assert make_jira_client(None, None, None) is None
