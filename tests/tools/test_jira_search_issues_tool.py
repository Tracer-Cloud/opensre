from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.JiraSearchIssuesTool import JiraSearchIssuesTool


def _tool() -> JiraSearchIssuesTool:
    return JiraSearchIssuesTool()


def test_is_available_with_connection_verified() -> None:
    assert _tool().is_available({"jira": {"connection_verified": True}}) is True


def test_is_available_false_without_connection_verified() -> None:
    assert _tool().is_available({"jira": {}}) is False
    assert _tool().is_available({}) is False


def test_extract_params_maps_source_fields() -> None:
    sources = {
        "jira": {
            "base_url": "https://myteam.atlassian.net",
            "email": "user@example.com",
            "api_token": "tok",
            "project_key": "OPS",
        }
    }
    params = _tool().extract_params(sources)
    assert params["base_url"] == "https://myteam.atlassian.net"
    assert params["email"] == "user@example.com"
    assert params["api_token"] == "tok"
    assert params["project_key"] == "OPS"


@patch("app.tools.JiraSearchIssuesTool.make_jira_client")
def test_run_returns_issues(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.search_issues.return_value = {
        "success": True,
        "issues": [
            {"issue_key": "OPS-1", "summary": "Bug", "status": "Open"},
        ],
        "total": 1,
    }
    mock_make.return_value = mock_client

    result = _tool().run(
        base_url="https://x.atlassian.net",
        email="u@e.com",
        api_token="tok",
        jql="project = OPS",
    )
    assert result["available"] is True
    assert result["total"] == 1
    assert result["issues"][0]["issue_key"] == "OPS-1"


@patch("app.tools.JiraSearchIssuesTool.make_jira_client")
def test_run_returns_unavailable_on_api_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.search_issues.return_value = {"success": False, "error": "HTTP 400"}
    mock_make.return_value = mock_client

    result = _tool().run(base_url="https://x.atlassian.net", email="u@e.com", api_token="tok")
    assert result["available"] is False
    assert "400" in result["error"]


@patch("app.tools.JiraSearchIssuesTool.make_jira_client")
def test_run_returns_unavailable_without_credentials(mock_make: MagicMock) -> None:
    mock_make.return_value = None
    result = _tool().run(base_url="", email="", api_token="")
    assert result["available"] is False


def test_metadata_is_valid() -> None:
    t = _tool()
    assert t.name == "jira_search_issues"
    assert t.source == "jira"
    assert "base_url" in t.input_schema["required"]
