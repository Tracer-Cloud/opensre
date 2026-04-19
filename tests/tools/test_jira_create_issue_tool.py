from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.JiraCreateIssueTool import JiraCreateIssueTool


def _tool() -> JiraCreateIssueTool:
    return JiraCreateIssueTool()


def test_is_available_with_connection_verified() -> None:
    assert _tool().is_available({"jira": {"connection_verified": True}}) is True


def test_is_available_false_without_connection_verified() -> None:
    assert _tool().is_available({"jira": {}}) is False
    assert _tool().is_available({}) is False


@patch("app.tools.JiraCreateIssueTool.make_jira_client")
def test_run_creates_issue(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.create_issue.return_value = {
        "success": True,
        "issue_key": "OPS-99",
        "issue_id": "10099",
        "url": "https://myteam.atlassian.net/browse/OPS-99",
    }
    mock_make.return_value = mock_client

    result = _tool().run(
        base_url="https://myteam.atlassian.net",
        email="u@e.com",
        api_token="tok",
        summary="Incident: API down",
        description="Root cause: DB connection pool exhausted.",
        project_key="OPS",
        labels=["incident", "rca"],
    )
    assert result["available"] is True
    assert result["issue_key"] == "OPS-99"
    assert "browse/OPS-99" in result["url"]
    mock_client.create_issue.assert_called_once_with(
        summary="Incident: API down",
        description="Root cause: DB connection pool exhausted.",
        issue_type="Bug",
        priority="High",
        labels=["incident", "rca"],
    )


@patch("app.tools.JiraCreateIssueTool.make_jira_client")
def test_run_returns_error_on_api_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.create_issue.return_value = {"success": False, "error": "HTTP 403"}
    mock_make.return_value = mock_client

    result = _tool().run(
        base_url="https://x.atlassian.net",
        email="u@e.com",
        api_token="tok",
        summary="test",
        description="test",
    )
    assert result["available"] is False
    assert "403" in result["error"]


@patch("app.tools.JiraCreateIssueTool.make_jira_client")
def test_run_returns_unavailable_without_credentials(mock_make: MagicMock) -> None:
    mock_make.return_value = None
    result = _tool().run(base_url="", email="", api_token="", summary="x", description="y")
    assert result["available"] is False


def test_metadata_is_valid() -> None:
    t = _tool()
    assert t.name == "jira_create_issue"
    assert t.source == "jira"
    assert "summary" in t.input_schema["required"]
    assert "description" in t.input_schema["required"]
