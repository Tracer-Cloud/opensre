"""Tests for SentryIssueDetailsTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.sentry.tools.sentry_issue_details_tool import (
    _map_get_sentry_issue_details,
    get_sentry_issue_details,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestSentryIssueDetailsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_sentry_issue_details.__opensre_registered_tool__


def test_is_available_requires_issue_id() -> None:
    rt = get_sentry_issue_details.__opensre_registered_tool__
    assert rt.is_available({"sentry": {"connection_verified": True, "issue_id": "123"}}) is True
    assert rt.is_available({"sentry": {"connection_verified": True}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_sentry_issue_details.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["issue_id"] == "12345"
    assert params["organization_slug"] == "my-org"


def test_run_returns_unavailable_when_no_config() -> None:
    result = get_sentry_issue_details(organization_slug="", sentry_token="", issue_id="123")
    assert result["available"] is False


def test_run_happy_path() -> None:
    fake_issue = {"id": "123", "title": "TypeError", "culprit": "app/views.py"}
    with (
        patch(
            "integrations.sentry.tools.sentry_issue_details_tool.get_sentry_issue",
            return_value=fake_issue,
        ),
        patch(
            "integrations.sentry.tools.sentry_search_issues_tool.sentry_config_from_env",
            return_value=None,
        ),
    ):
        result = get_sentry_issue_details(
            organization_slug="my-org", sentry_token="tok_test", issue_id="123"
        )
    assert result["available"] is True
    assert result["issue"]["id"] == "123"


class TestMapGetSentryIssueDetails:
    def test_records_entry_with_level_status_and_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_sentry_issue_details(
            evidence,
            {
                "available": True,
                "issue": {
                    "title": "TypeError: cannot read property",
                    "culprit": "app/views.py",
                    "level": "error",
                    "status": "unresolved",
                    "count": "42",
                },
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_sentry_issue_details"
        assert (
            entries[0]["summary"]
            == "'TypeError: cannot read property', level error, unresolved, 42 event(s)"
        )

    def test_truncates_long_multiline_title(self) -> None:
        """Regression: a raw exception message can be long and multi-line —
        collapse and cap it so it can't produce a malformed report line."""
        evidence: dict[str, Any] = {}
        long_title = "TypeError: boom\n  at foo (bar.ts:1)\n" + "x" * 200

        _map_get_sentry_issue_details(
            evidence,
            {"available": True, "issue": {"title": long_title, "level": "error"}},
            {},
        )

        summary = evidence["catalog_entries"][0]["summary"]
        assert "\n" not in summary
        assert len(summary) < len(long_title)

    def test_records_nothing_when_issue_empty(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_sentry_issue_details(evidence, {"available": True, "issue": {}}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_sentry_issue_details(evidence, {"available": False, "error": "not configured"}, {})

        assert "catalog_entries" not in evidence
