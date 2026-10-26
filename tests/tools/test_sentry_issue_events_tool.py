"""Tests for SentryIssueEventsTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.sentry.tools.sentry_issue_events_tool import (
    _map_list_sentry_issue_events,
    list_sentry_issue_events,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestSentryIssueEventsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_sentry_issue_events.__opensre_registered_tool__


def test_is_available_requires_issue_id() -> None:
    rt = list_sentry_issue_events.__opensre_registered_tool__
    assert rt.is_available({"sentry": {"connection_verified": True, "issue_id": "123"}}) is True
    assert rt.is_available({"sentry": {"connection_verified": True}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = list_sentry_issue_events.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["issue_id"] == "12345"
    assert params["organization_slug"] == "my-org"


def test_run_returns_unavailable_when_no_config() -> None:
    result = list_sentry_issue_events(organization_slug="", sentry_token="", issue_id="123")
    assert result["available"] is False
    assert result["events"] == []


def test_run_happy_path() -> None:
    fake_events = [{"eventID": "e1", "dateCreated": "2024-01-01"}]
    with (
        patch(
            "integrations.sentry.tools.sentry_issue_events_tool.sentry_list_issue_events",
            return_value=fake_events,
        ),
        patch(
            "integrations.sentry.tools.sentry_search_issues_tool.sentry_config_from_env",
            return_value=None,
        ),
    ):
        result = list_sentry_issue_events(
            organization_slug="my-org", sentry_token="tok_test", issue_id="123"
        )
    assert result["available"] is True
    assert len(result["events"]) == 1


class TestMapListSentryIssueEvents:
    def test_records_entry_with_event_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_sentry_issue_events(
            evidence,
            {
                "available": True,
                "events": [
                    {"eventID": "e1", "dateCreated": "2024-01-02"},
                    {"eventID": "e2", "dateCreated": "2024-01-01"},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "list_sentry_issue_events"
        assert entries[0]["summary"] == "2 recent event(s) retrieved"

    def test_records_nothing_when_no_events(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_sentry_issue_events(evidence, {"available": True, "events": []}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_sentry_issue_events(evidence, {"available": False, "error": "not configured"}, {})

        assert "catalog_entries" not in evidence
