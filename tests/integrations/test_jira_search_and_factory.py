"""Tests for JiraClient.search_issues() and make_jira_client() factory."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import httpx
import pytest

from integrations.config_models import JiraIntegrationConfig as JiraConfig
from integrations.jira.client import JiraClient, make_jira_client


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


def test_search_issues_posts_to_enhanced_jql_search(client: JiraClient) -> None:
    mock_resp = MagicMock()
    # The enhanced search response pages with nextPageToken and carries no total.
    mock_resp.json.return_value = {"issues": [{"key": "OPS-1", "fields": {}}], "isLast": True}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        result = client.search_issues(jql="project = OPS")

    url = mock_post.call_args.args[0]
    assert url == "https://myteam.atlassian.net/rest/api/3/search/jql"
    assert result["success"] is True
    assert result["total"] == 1
    assert result["truncated"] is False


def test_search_issues_flags_partial_page_without_reporting_page_length_as_total(
    client: JiraClient,
) -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "issues": [{"key": f"OPS-{n}", "fields": {}} for n in range(20)],
        "isLast": False,
        "nextPageToken": "page-2",
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client.post", return_value=mock_resp):
        result = client.search_issues(jql="project = OPS")

    assert len(result["issues"]) == 20
    assert result["truncated"] is True
    assert result["total"] is None
    assert result["next_page_token"] == "page-2"


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


def _raise_runtime_error(**_kwargs: object) -> None:
    raise RuntimeError("construction failure")


def test_make_jira_client_logs_soft_fail_on_construction_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Arrange: force config construction inside the factory to raise, exercising
    # the soft-fail `except Exception` path. The factory must still return None,
    # but must no longer swallow the exception silently.
    monkeypatch.setattr("integrations.jira.client.JiraIntegrationConfig", _raise_runtime_error)

    # Act
    with caplog.at_level(logging.WARNING, logger="integrations.jira.client"):
        result = make_jira_client("https://myteam.atlassian.net", "user@example.com", "token")

    # Assert
    assert result is None
    assert any(
        record.levelno == logging.WARNING and record.exc_info is not None
        for record in caplog.records
    ), caplog.text


_SECRET_API_TOKEN = "s3cr3t-jira-api-token"


def _raise_validation_error(**_kwargs: object) -> None:
    # A genuine pydantic ValidationError raised against the model rather than a
    # single field, so its rendering carries the whole input mapping — this is
    # the shape that would leak `api_token` through `input_value=`.
    JiraConfig(email="user@example.com", api_token=_SECRET_API_TOKEN)  # type: ignore[call-arg]


def test_make_jira_client_soft_fail_log_does_not_echo_config_input(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Arrange: no input the factory currently accepts can make JiraIntegrationConfig
    # raise — every field is coerced, and there is no URL-scheme validator as there
    # is on the ServiceNow model. So the ValidationError has to be injected. The
    # guard is hardening: it stops a future validator (or a caller passing a
    # non-string) from turning this warning into a credential disclosure.
    monkeypatch.setattr("integrations.jira.client.JiraIntegrationConfig", _raise_validation_error)

    # Act
    with caplog.at_level(logging.WARNING, logger="integrations.jira.client"):
        result = make_jira_client(
            "https://myteam.atlassian.net", "user@example.com", _SECRET_API_TOKEN
        )

    # Assert: the soft-fail contract and the warning both survive, but no part of
    # the submitted config is echoed. Asserting on `input_value` rather than only
    # on the literal secret is deliberate: pydantic elides the middle of a long
    # mapping, so a secret-substring check can pass by accident and would stop
    # protecting us if field order or pydantic's truncation changed.
    assert result is None
    assert any(
        record.levelno == logging.WARNING and record.exc_info is not None
        for record in caplog.records
    ), caplog.text
    assert "input_value" not in caplog.text
    assert _SECRET_API_TOKEN not in caplog.text
