"""Tests for summarize_community_followups (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from integrations.github.tools.community_followup_tool import summarize_community_followups
from tests.tools.conftest import BaseToolContract


class TestSummarizeCommunityFollowupsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return summarize_community_followups.__opensre_registered_tool__


def test_run_scopes_the_comments_query_to_since_days() -> None:
    """Regression: the endpoint returns every comment across the whole repo
    (not one issue), so an unscoped query could run to thousands of pages on
    an active repo. since_days must bound it via GitHub's `since` param."""
    mock_client = MagicMock()
    mock_client.paginate.return_value = []
    with patch(
        "integrations.github.tools.community_followup_tool.GitHubRestClient",
        return_value=mock_client,
    ):
        summarize_community_followups(
            github_connection_origin="webapp",
            owner="org",
            repo="repo",
            since_days=7,
            github_token="tok",
        )

    mock_client.paginate.assert_called_once()
    args, kwargs = mock_client.paginate.call_args
    assert args[0] == "/repos/org/repo/issues/comments"
    assert "since" in kwargs["params"]
    assert kwargs["params"]["sort"] == "updated"


def test_run_skips_the_api_call_when_comments_are_supplied() -> None:
    result = summarize_community_followups(
        github_connection_origin="webapp",
        github_token="app-token",
        owner="org",
        repo="repo",
        comments=[{"id": 1, "body": "any questions?", "user": {"login": "someone"}}],
    )
    assert result["available"] is True


def test_run_clamps_absurd_since_days_instead_of_overflowing() -> None:
    """Regression (Greptile): since_days was only lower-bounded before being
    passed to timedelta(days=...). An absurdly large caller-supplied value
    (the public schema accepts any integer) raises OverflowError, which
    propagates uncaught past the tool framework's structured
    tool_unavailable() error contract entirely."""
    mock_client = MagicMock()
    mock_client.paginate.return_value = []
    with patch(
        "integrations.github.tools.community_followup_tool.GitHubRestClient",
        return_value=mock_client,
    ):
        result = summarize_community_followups(
            github_connection_origin="webapp",
            owner="org",
            repo="repo",
            since_days=10**18,
            github_token="tok",
        )

    assert result["available"] is True
    mock_client.paginate.assert_called_once()
