from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.JiraAddCommentTool import JiraAddCommentTool


def _tool() -> JiraAddCommentTool:
    return JiraAddCommentTool()


def test_is_available_with_connection_verified() -> None:
    assert _tool().is_available({"jira": {"connection_verified": True}}) is True


def test_is_available_false_without_connection_verified() -> None:
    assert _tool().is_available({"jira": {}}) is False
    assert _tool().is_available({}) is False


@patch("app.tools.JiraAddCommentTool.make_jira_client")
def test_run_adds_comment(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.add_comment.return_value = {"success": True, "comment_id": "comment-55"}
    mock_make.return_value = mock_client

    result = _tool().run(
        base_url="https://x.atlassian.net",
        email="u@e.com",
        api_token="tok",
        issue_key="OPS-42",
        body="RCA complete. Root cause: pool exhaustion.",
    )
    assert result["available"] is True
    assert result["comment_id"] == "comment-55"
    assert result["issue_key"] == "OPS-42"


@patch("app.tools.JiraAddCommentTool.make_jira_client")
def test_run_returns_error_on_api_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.add_comment.return_value = {"success": False, "error": "HTTP 403"}
    mock_make.return_value = mock_client

    result = _tool().run(
        base_url="https://x.atlassian.net",
        email="u@e.com",
        api_token="tok",
        issue_key="OPS-42",
        body="test comment",
    )
    assert result["available"] is False
    assert "403" in result["error"]


def test_run_returns_error_without_issue_key() -> None:
    result = _tool().run(
        base_url="https://x.atlassian.net",
        email="u@e.com",
        api_token="tok",
        issue_key="",
        body="test",
    )
    assert result["available"] is False
    assert "issue_key" in result["error"]


def test_run_returns_error_without_body() -> None:
    result = _tool().run(
        base_url="https://x.atlassian.net",
        email="u@e.com",
        api_token="tok",
        issue_key="OPS-42",
        body="",
    )
    assert result["available"] is False
    assert "body" in result["error"]


@patch("app.tools.JiraAddCommentTool.make_jira_client")
def test_run_returns_unavailable_without_credentials(mock_make: MagicMock) -> None:
    mock_make.return_value = None
    result = _tool().run(
        base_url="", email="", api_token="", issue_key="OPS-1", body="test"
    )
    assert result["available"] is False


def test_metadata_is_valid() -> None:
    t = _tool()
    assert t.name == "jira_add_comment"
    assert t.source == "jira"
    assert "issue_key" in t.input_schema["required"]
    assert "body" in t.input_schema["required"]
