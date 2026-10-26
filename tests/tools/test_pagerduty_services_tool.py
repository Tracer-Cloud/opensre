"""Unit tests for PagerDutyServicesTool."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from integrations.pagerduty.tools import PagerDutyServicesTool, _map_pagerduty_services


def _tool() -> PagerDutyServicesTool:
    return PagerDutyServicesTool()


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
    assert params["service_id"] == ""


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_lists_services(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_services.return_value = {
        "success": True,
        "services": [
            {
                "id": "SVC1",
                "name": "Web App",
                "status": "active",
                "escalation_policy": {"id": "EP1", "summary": "Prod", "type": "ep_ref"},
                "teams": [],
                "alert_creation": "create_alerts_and_incidents",
                "integrations": [{"id": "I1", "name": "Datadog", "type": "generic_events_api"}],
                "html_url": "https://app.pagerduty.com/services/SVC1",
            },
        ],
        "total": 1,
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is True
    assert result["total"] == 1
    assert result["services"][0]["name"] == "Web App"
    assert result["service"] == {}


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_gets_service_detail_when_id_provided(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_service.return_value = {
        "success": True,
        "service": {
            "id": "SVC1",
            "name": "Web App",
            "description": "Main web application",
            "status": "active",
            "escalation_policy": {"id": "EP1", "summary": "Prod", "type": "ep_ref"},
            "teams": [],
            "alert_creation": "create_alerts_and_incidents",
            "incident_urgency_rule": {"type": "constant", "urgency": "high"},
            "integrations": [],
            "html_url": "https://app.pagerduty.com/services/SVC1",
        },
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", service_id="SVC1")
    assert result["available"] is True
    assert result["service"]["name"] == "Web App"
    assert result["service"]["incident_urgency_rule"]["urgency"] == "high"
    assert result["services"] == []
    assert result["total"] == 1


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_empty_services_list(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_services.return_value = {"success": True, "services": [], "total": 0}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is True
    assert result["services"] == []
    assert result["total"] == 0


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_returns_unavailable_on_list_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_services.return_value = {"success": False, "error": "HTTP 401: Unauthorized"}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is False
    assert "401" in result["error"]


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_returns_unavailable_on_detail_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_service.return_value = {"success": False, "error": "HTTP 404: Not Found"}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", service_id="bad-id")
    assert result["available"] is False
    assert "404" in result["error"]


@patch("integrations.pagerduty.tools.make_pagerduty_client")
def test_run_returns_unavailable_without_key(mock_make: MagicMock) -> None:
    mock_make.return_value = None
    result = _tool().run(api_key="")
    assert result["available"] is False


def test_metadata_is_valid() -> None:
    t = _tool()
    assert t.name == "pagerduty_services"
    assert t.source == "pagerduty"
    assert "api_key" in t.input_schema["required"]


class TestMapPagerdutyServices:
    def test_records_entry_for_single_service_detail(self) -> None:
        evidence: dict[str, Any] = {}

        _map_pagerduty_services(
            evidence,
            {
                "available": True,
                "services": [],
                "service": {"name": "Web App", "status": "active"},
                "total": 1,
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "pagerduty_services"
        assert entries[0]["summary"] == "'Web App': active"

    def test_truncates_long_multiline_service_name(self) -> None:
        """Regression: service names are free-form, human-entered text that
        can be long or multi-line — collapse and cap it before it goes into
        the report summary."""
        evidence: dict[str, Any] = {}
        long_name = "Web App\nProduction\n" + "x" * 200

        _map_pagerduty_services(
            evidence,
            {
                "available": True,
                "services": [],
                "service": {"name": long_name, "status": "active"},
                "total": 1,
            },
            {},
        )

        summary = evidence["catalog_entries"][0]["summary"]
        assert "\n" not in summary
        assert len(summary) < len(long_name)

    def test_strips_carriage_returns_from_service_name(self) -> None:
        """Regression: a name with bare \\r or \\r\\n line endings must not
        leave a literal carriage return in the report summary."""
        evidence: dict[str, Any] = {}

        _map_pagerduty_services(
            evidence,
            {
                "available": True,
                "services": [],
                "service": {"name": "Web App\r\nProduction\r", "status": "active"},
                "total": 1,
            },
            {},
        )

        summary = evidence["catalog_entries"][0]["summary"]
        assert "\r" not in summary

    def test_records_entry_with_service_count_when_listing(self) -> None:
        evidence: dict[str, Any] = {}

        _map_pagerduty_services(
            evidence,
            {
                "available": True,
                "services": [{"name": "Web App"}, {"name": "API"}],
                "service": {},
                "total": 2,
            },
            {"limit": 25},
        )

        assert evidence["catalog_entries"][0]["summary"] == "2 service(s)"

    def test_qualifies_count_when_saturated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_pagerduty_services(
            evidence,
            {
                "available": True,
                "services": [{"name": str(i)} for i in range(25)],
                "service": {},
                "total": 25,
            },
            {"limit": 25},
        )

        assert evidence["catalog_entries"][0]["summary"] == "25+ service(s)"

    def test_records_nothing_when_no_services_and_no_service(self) -> None:
        evidence: dict[str, Any] = {}

        _map_pagerduty_services(
            evidence, {"available": True, "services": [], "service": {}, "total": 0}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_pagerduty_services(evidence, {"available": False, "error": "HTTP 401"}, {})

        assert "catalog_entries" not in evidence
