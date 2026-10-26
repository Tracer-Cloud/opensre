"""Unit tests for PagerDutyIncidentsTool."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from integrations.pagerduty.tools import PagerDutyIncidentsTool, _map_pagerduty_incidents


def _tool() -> PagerDutyIncidentsTool:
    return PagerDutyIncidentsTool()


def test_is_available_with_connection_verified() -> None:
    assert _tool().is_available({"pagerduty": {"connection_verified": True}}) is True


def test_is_available_false_without_connection_verified() -> None:
    assert _tool().is_available({"pagerduty": {}}) is False
    assert _tool().is_available({}) is False


def test_extract_params_maps_source_fields() -> None:
    sources = {
        "pagerduty": {
            "api_key": "pd-key",
            "base_url": "https://api.pagerduty.com",
        }
    }
    params = _tool().extract_params(sources)
    assert params["api_key"] == "pd-key"
    assert params["base_url"] == "https://api.pagerduty.com"


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_returns_incidents_and_active_subset(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_incidents.return_value = {
        "success": True,
        "incidents": [
            {"id": "P1", "status": "triggered", "urgency": "high"},
            {"id": "P2", "status": "acknowledged", "urgency": "high"},
            {"id": "P3", "status": "resolved", "urgency": "low"},
        ],
        "total": 3,
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is True
    assert result["total"] == 3
    assert len(result["active_incidents"]) == 2
    assert result["active_incidents"][0]["id"] == "P1"
    assert result["active_incidents"][1]["id"] == "P2"


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_empty_incidents_list(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_incidents.return_value = {"success": True, "incidents": [], "total": 0}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is True
    assert result["incidents"] == []
    assert result["active_incidents"] == []


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_returns_unavailable_on_api_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_incidents.return_value = {"success": False, "error": "HTTP 401: Unauthorized"}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is False
    assert "401" in result["error"]


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_returns_unavailable_without_key(mock_make: MagicMock) -> None:
    mock_make.return_value = None
    result = _tool().run(api_key="")
    assert result["available"] is False


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_passes_filter_params(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_incidents.return_value = {"success": True, "incidents": [], "total": 0}
    mock_make.return_value = mock_client

    _tool().run(
        api_key="k",
        statuses=["triggered"],
        urgencies=["high"],
        service_ids=["SVC1"],
        since="2024-01-01T00:00:00Z",
        until="2024-01-02T00:00:00Z",
        limit=10,
    )
    mock_client.list_incidents.assert_called_once_with(
        statuses=["triggered"],
        urgencies=["high"],
        service_ids=["SVC1"],
        since="2024-01-01T00:00:00Z",
        until="2024-01-02T00:00:00Z",
        limit=10,
    )


def test_metadata_is_valid() -> None:
    t = _tool()
    assert t.name == "pagerduty_incidents"
    assert t.source == "pagerduty"
    assert "api_key" in t.input_schema["required"]


class TestMapPagerdutyIncidents:
    def test_records_entry_with_active_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_pagerduty_incidents(
            evidence,
            {
                "available": True,
                "total": 3,
                "incidents": [{"id": "P1"}, {"id": "P2"}, {"id": "P3"}],
                "active_incidents": [{"id": "P1"}, {"id": "P2"}],
            },
            {"limit": 25},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "pagerduty_incidents"
        assert entries[0]["summary"] == "3 incident(s), 2 active"

    def test_qualifies_count_when_saturated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_pagerduty_incidents(
            evidence,
            {
                "available": True,
                "total": 25,
                "incidents": [{"id": str(i)} for i in range(25)],
                "active_incidents": [],
            },
            {"limit": 25},
        )

        assert evidence["catalog_entries"][0]["summary"] == "25+ incident(s)"

    def test_qualifies_active_count_when_page_is_truncated(self) -> None:
        """Regression: active_incidents is filtered from the same page-capped
        list, so it must inherit the "+" qualifier when the page saturates —
        an unqualified active count would understate incidents outside the
        page that could also be active."""
        evidence: dict[str, Any] = {}

        _map_pagerduty_incidents(
            evidence,
            {
                "available": True,
                "total": 25,
                "incidents": [{"id": str(i)} for i in range(25)],
                "active_incidents": [{"id": str(i)} for i in range(10)],
            },
            {"limit": 25},
        )

        assert evidence["catalog_entries"][0]["summary"] == "25+ incident(s), 10+ active"

    def test_records_nothing_when_no_incidents(self) -> None:
        evidence: dict[str, Any] = {}

        _map_pagerduty_incidents(
            evidence, {"available": True, "total": 0, "incidents": [], "active_incidents": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_pagerduty_incidents(evidence, {"available": False, "error": "HTTP 401"}, {})

        assert "catalog_entries" not in evidence
