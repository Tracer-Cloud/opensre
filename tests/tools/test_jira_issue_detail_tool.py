from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.JiraIssueDetailTool import JiraIssueDetailTool


def _tool() -> JiraIssueDetailTool:
    return JiraIssueDetailTool()


def test_is_available_with_connection_verified() -> None:
    assert _tool().is_available({"jira": {"connection_verified": True}}) is True


def test_is_available_false_without_connection_verified() -> None:
    assert _tool().is_available({"jira": {}}) is False
    assert _tool().is_available({}) is False


@patch("app.tools.JiraIssueDetailTool.make_jira_client")
def test_run_returns_issue_detail(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_issue.return_value = {
        "success": True,
        "issue_key": "OPS-42",
        "summary": "DB spike",
        "status": "Open",
        "priority": "High",
        "labels": ["incident"],
        "description": "some desc",
    }
    mock_make.return_value = mock_client

    result = _tool().run(
        base_url="https://x.atlassian.net",
        email="u@e.com",
        api_token="tok",
        issue_key="OPS-42",
    )
    assert result["available"] is True
    assert result["issue"]["issue_key"] == "OPS-42"
    assert result["issue"]["status"] == "Open"


@patch("app.tools.JiraIssueDetailTool.make_jira_client")
def test_run_returns_error_on_api_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_issue.return_value = {"success": False, "error": "HTTP 404"}
    mock_make.return_value = mock_client

    result = _tool().run(
        base_url="https://x.atlassian.net",
        email="u@e.com",
        api_token="tok",
        issue_key="OPS-999",
    )
    assert result["available"] is False
    assert "404" in result["error"]


def test_run_returns_error_without_issue_key() -> None:
    result = _tool().run(
        base_url="https://x.atlassian.net",
        email="u@e.com",
        api_token="tok",
        issue_key="",
    )
    assert result["available"] is False
    assert "issue_key" in result["error"]


@patch("app.tools.JiraIssueDetailTool.make_jira_client")
def test_run_returns_unavailable_without_credentials(mock_make: MagicMock) -> None:
    mock_make.return_value = None
    result = _tool().run(base_url="", email="", api_token="", issue_key="OPS-1")
    assert result["available"] is False


def test_metadata_is_valid() -> None:
    t = _tool()
    assert t.name == "jira_issue_detail"
    assert t.source == "jira"
    assert "issue_key" in t.input_schema["required"]
