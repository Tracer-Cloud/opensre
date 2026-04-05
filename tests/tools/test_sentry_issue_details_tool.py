"""Tests for SentryIssueDetailsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.SentryIssueDetailsTool import get_sentry_issue_details
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestSentryIssueDetailsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_sentry_issue_details.__opensre_registered_tool__


def test_is_available_requires_issue_id() -> None:
    rt = get_sentry_issue_details.__opensre_registered_tool__
    assert rt.is_available({
        "sentry": {"connection_verified": True, "issue_id": "123"}
    }) is True
    assert rt.is_available({"sentry": {"connection_verified": True}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_sentry_issue_details.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["issue_id"] == "12345"
    assert params["organization_slug"] == "my-org"


def test_run_returns_unavailable_when_no_config() -> None:
    result = get_sentry_issue_details(
        organization_slug="", sentry_token="", issue_id="123"
    )
    assert result["available"] is False


def test_run_happy_path() -> None:
    fake_issue = {"id": "123", "title": "TypeError", "culprit": "app/views.py"}
    with patch("app.tools.SentryIssueDetailsTool.get_sentry_issue", return_value=fake_issue), \
         patch("app.tools.SentrySearchIssuesTool.sentry_config_from_env", return_value=None):
        result = get_sentry_issue_details(
            organization_slug="my-org", sentry_token="tok_test", issue_id="123"
        )
    assert result["available"] is True
    assert result["issue"]["id"] == "123"
