"""Unit tests for PagerDutyIncidentDetailTool."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from integrations.pagerduty.tools import PagerDutyIncidentDetailTool, _map_pagerduty_incident_detail


def _tool() -> PagerDutyIncidentDetailTool:
    return PagerDutyIncidentDetailTool()


def test_is_available_requires_connection_verified() -> None:
    assert _tool().is_available({"pagerduty": {"connection_verified": True}}) is True
    assert _tool().is_available({"pagerduty": {}}) is False
    assert _tool().is_available({}) is False


def test_extract_params_maps_source_fields() -> None:
    sources = {
        "pagerduty": {
            "api_key": "pd-key",
            "base_url": "https://api.pagerduty.com",
            "incident_id": "P123ABC",
        }
    }
    params = _tool().extract_params(sources)
    assert params["api_key"] == "pd-key"
    assert params["incident_id"] == "P123ABC"


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_returns_incident_and_log_entries(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_incident.return_value = {
        "success": True,
        "incident": {"id": "P1", "title": "CPU spike", "status": "triggered"},
    }
    mock_client.list_incident_log_entries.return_value = {
        "success": True,
        "log_entries": [
            {"id": "L1", "type": "trigger_log_entry", "summary": "Triggered via API"},
        ],
        "total": 1,
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", incident_id="P1")
    assert result["available"] is True
    assert result["incident"]["title"] == "CPU spike"
    assert len(result["log_entries"]) == 1
    assert result["total_log_entries"] == 1


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_skips_log_entries_when_disabled(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_incident.return_value = {
        "success": True,
        "incident": {"id": "P1", "title": "test"},
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", incident_id="P1", include_log_entries=False)
    assert result["available"] is True
    assert result["log_entries"] == []
    mock_client.list_incident_log_entries.assert_not_called()


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_handles_incident_fetch_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_incident.return_value = {"success": False, "error": "HTTP 404: Not Found"}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", incident_id="bad-id")
    assert result["available"] is False
    assert "404" in result["error"]


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_handles_log_entries_failure_gracefully(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_incident.return_value = {
        "success": True,
        "incident": {"id": "P1", "title": "test"},
    }
    mock_client.list_incident_log_entries.return_value = {
        "success": False,
        "error": "timeout",
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", incident_id="P1")
    assert result["available"] is True
    assert result["log_entries"] == []


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_returns_unavailable_without_key(mock_make: MagicMock) -> None:
    mock_make.return_value = None
    result = _tool().run(api_key="", incident_id="P1")
    assert result["available"] is False


def test_run_returns_error_without_incident_id() -> None:
    result = _tool().run(api_key="k", incident_id="")
    assert result["available"] is False
    assert "incident_id is required" in result["error"]


def test_metadata_requires_incident_id() -> None:
    t = _tool()
    assert t.name == "pagerduty_incident_detail"
    assert "incident_id" in t.input_schema["required"]


class TestMapPagerdutyIncidentDetail:
    def test_records_entry_with_timeline_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_pagerduty_incident_detail(
            evidence,
            {
                "available": True,
                "incident": {"title": "CPU spike", "status": "triggered"},
                "log_entries": [{"id": "L1"}, {"id": "L2"}],
                "total_log_entries": 2,
            },
            {"log_limit": 25},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "pagerduty_incident_detail"
        assert entries[0]["summary"] == "'CPU spike', triggered, 2 timeline entries"

    def test_qualifies_timeline_count_when_saturated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_pagerduty_incident_detail(
            evidence,
            {
                "available": True,
                "incident": {"title": "CPU spike", "status": "triggered"},
                "log_entries": [{"id": str(i)} for i in range(25)],
                "total_log_entries": 25,
            },
            {"log_limit": 25},
        )

        assert (
            evidence["catalog_entries"][0]["summary"]
            == "'CPU spike', triggered, 25+ timeline entries"
        )

    def test_records_entry_without_timeline_clause_when_none_fetched(self) -> None:
        evidence: dict[str, Any] = {}

        _map_pagerduty_incident_detail(
            evidence,
            {
                "available": True,
                "incident": {"title": "CPU spike", "status": "resolved"},
                "log_entries": [],
                "total_log_entries": 0,
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "'CPU spike', resolved"

    def test_truncates_long_multiline_title(self) -> None:
        """Regression: incident titles are free-form, human-entered text that
        can be long or multi-line — collapse and cap it before it goes into
        the report summary."""
        evidence: dict[str, Any] = {}
        long_title = "CPU spike\non host-42\n" + "x" * 200

        _map_pagerduty_incident_detail(
            evidence,
            {"available": True, "incident": {"title": long_title, "status": "triggered"}},
            {},
        )

        summary = evidence["catalog_entries"][0]["summary"]
        assert "\n" not in summary
        assert len(summary) < len(long_title)

    def test_strips_carriage_returns_from_title(self) -> None:
        """Regression: a title with bare \\r or \\r\\n line endings must not
        leave a literal carriage return in the report summary."""
        evidence: dict[str, Any] = {}

        _map_pagerduty_incident_detail(
            evidence,
            {
                "available": True,
                "incident": {"title": "CPU spike\r\non host-42\r", "status": "triggered"},
            },
            {},
        )

        summary = evidence["catalog_entries"][0]["summary"]
        assert "\r" not in summary

    def test_records_nothing_when_incident_empty(self) -> None:
        evidence: dict[str, Any] = {}

        _map_pagerduty_incident_detail(evidence, {"available": True, "incident": {}}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_pagerduty_incident_detail(
            evidence, {"available": False, "error": "not configured"}, {}
        )

        assert "catalog_entries" not in evidence
