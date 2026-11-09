"""Tests for NewRelicAlertsTool (class-based, BaseTool subclass).

Uses the synthetic fixture at ``tests/fixtures/new_relic/incidents_response.json``
(T-01c), which reproduces the real-account shape discovered in spec.md Decision 2
with entirely fictitious names: lowercase *and* capitalized ``event`` values (the
official docs show ``Open``/``Close`` capitalized, the real account returned
lowercase ``open``/``close`` — the parser must accept both), an open+close pair
sharing one ``incidentId``, three distinct ``incidentId``s flapping on the same
condition/entity, HTML-entity-encoded ``operator``/``title``, and ``threshold``
as a JSON string.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from integrations.new_relic.tools.new_relic_alerts_tool.results import parse_incident_rows
from integrations.new_relic.tools.new_relic_alerts_tool.tool import (
    NewRelicAlertsTool,
    _map_query_new_relic_alerts,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "new_relic" / "incidents_response.json"
)


def _load_fixture_rows() -> list[dict[str, Any]]:
    payload = json.loads(_FIXTURE_PATH.read_text())
    return payload["data"]["actor"]["account"]["nrql"]["results"]  # type: ignore[no-any-return]


class TestNewRelicAlertsToolContract(BaseToolContract):
    def get_tool_under_test(self) -> NewRelicAlertsTool:
        return NewRelicAlertsTool()


def test_is_available_requires_connection_verified_and_account_id() -> None:
    tool = NewRelicAlertsTool()
    assert (
        tool.is_available({"new_relic": {"connection_verified": True, "account_id": "123"}}) is True
    )
    assert tool.is_available({"new_relic": {"connection_verified": True}}) is False
    assert tool.is_available({"new_relic": {"account_id": "123"}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_credentials() -> None:
    tool = NewRelicAlertsTool()
    sources = mock_agent_state(
        {
            "new_relic": {
                "connection_verified": True,
                "api_key": "NRAK-test",
                "account_id": "123456",
                "base_url": "https://api.newrelic.com",
            }
        }
    )
    params = tool.extract_params(sources)
    assert params["api_key"] == "NRAK-test"
    assert params["account_id"] == "123456"
    assert params["base_url"] == "https://api.newrelic.com"


def test_parse_incident_rows_pairs_open_and_close_by_incident_id() -> None:
    rows = _load_fixture_rows()
    incidents, truncated = parse_incident_rows(rows, requested_limit=100)

    by_id = {incident["incident_id"]: incident for incident in incidents}
    assert set(by_id) == {
        "inc-fixture-1001",
        "inc-fixture-1002",
        "inc-fixture-1003",
        "inc-fixture-2001",
    }
    assert truncated is False

    # inc-fixture-1001 (lowercase "open"/"close") pairs into one closed record,
    # using the close row's context per Decision 2.
    closed = by_id["inc-fixture-1001"]
    assert closed["status"] == "closed"
    assert closed["title"] == "Checkout latency >= 3.0s (resolved)"
    assert closed["close_time"] == 1754905500000

    # inc-fixture-1002 uses the docs' capitalized "Open"/"Close" — the parser
    # must pair it identically to the lowercase real-account form above.
    closed_capitalized = by_id["inc-fixture-1002"]
    assert closed_capitalized["status"] == "closed"
    assert closed_capitalized["close_time"] == 1754906700000


def test_parse_incident_rows_does_not_collapse_flapping_incidents() -> None:
    """Three distinct incidentIds on the same condition+entity stay three records."""
    rows = _load_fixture_rows()
    incidents, _truncated = parse_incident_rows(rows, requested_limit=100)

    flapping = [
        incident
        for incident in incidents
        if incident["condition_name"] == "checkout-latency"
        and incident["entity_name"] == "checkout-service"
    ]
    assert len(flapping) == 3
    assert {incident["incident_id"] for incident in flapping} == {
        "inc-fixture-1001",
        "inc-fixture-1002",
        "inc-fixture-1003",
    }


def test_parse_incident_rows_still_open_incident_has_no_close_time() -> None:
    rows = _load_fixture_rows()
    incidents, _truncated = parse_incident_rows(rows, requested_limit=100)
    by_id = {incident["incident_id"]: incident for incident in incidents}

    still_open = by_id["inc-fixture-1003"]
    assert still_open["status"] == "open"
    assert still_open["close_time"] is None
    assert still_open["muted"] is True  # muted stays flagged, never filtered out

    other_open = by_id["inc-fixture-2001"]
    assert other_open["status"] == "open"
    assert other_open["muted"] is False
    assert other_open["runbook_url"] is None  # nullable field, must not crash


def test_parse_incident_rows_decodes_html_entities_and_parses_threshold() -> None:
    rows = _load_fixture_rows()
    incidents, _truncated = parse_incident_rows(rows, requested_limit=100)
    by_id = {incident["incident_id"]: incident for incident in incidents}

    incident = by_id["inc-fixture-1001"]
    assert incident["operator"] == ">="
    assert "&gt;" not in incident["title"]
    assert incident["threshold"] == 3.0
    assert incident["threshold_raw"] == "3.0"


def test_parse_incident_rows_preserves_causal_order() -> None:
    rows = _load_fixture_rows()
    incidents, _truncated = parse_incident_rows(rows, requested_limit=100)
    open_times = [incident["open_time"] for incident in incidents]
    assert open_times == sorted(open_times)


def test_parse_incident_rows_flags_truncation_when_cap_is_hit() -> None:
    rows = _load_fixture_rows()
    _incidents, truncated = parse_incident_rows(rows, requested_limit=len(rows))
    assert truncated is True


def test_run_returns_unavailable_when_not_configured() -> None:
    tool = NewRelicAlertsTool()
    result = tool.run(api_key="", account_id="", base_url="")
    assert result["available"] is False


def test_run_happy_path_uses_fixture_rows() -> None:
    tool = NewRelicAlertsTool()
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_incidents.return_value = {"success": True, "results": _load_fixture_rows()}
    with patch(
        "integrations.new_relic.tools.new_relic_alerts_tool.tool.NewRelicClient",
        return_value=mock_client,
    ):
        result = tool.run(api_key="NRAK-test", account_id="123456")
    assert result["available"] is True
    assert result["total"] == 4


def test_run_flags_truncation_against_the_clamped_limit_not_the_request() -> None:
    """A full response at the vendor's 5,000-row ceiling must still read as
    truncated, even though it falls short of a caller-requested limit above
    that ceiling — the client reports back the limit it actually executed."""
    tool = NewRelicAlertsTool()
    mock_client = MagicMock()
    mock_client.is_configured = True
    capped_rows = [{"incidentId": str(i), "event": "open", "openTime": i} for i in range(2)]
    mock_client.query_incidents.return_value = {
        "success": True,
        "results": capped_rows,
        "effective_limit": 2,
    }
    with patch(
        "integrations.new_relic.tools.new_relic_alerts_tool.tool.NewRelicClient",
        return_value=mock_client,
    ):
        result = tool.run(api_key="NRAK-test", account_id="123456", limit=999_999)
    assert result["truncated"] is True


def test_run_api_error_is_reported_as_unavailable() -> None:
    tool = NewRelicAlertsTool()
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_incidents.return_value = {
        "success": False,
        "error": "New Relic query timed out after 5s — narrow the time window or query.",
        "error_type": "timeout",
    }
    with patch(
        "integrations.new_relic.tools.new_relic_alerts_tool.tool.NewRelicClient",
        return_value=mock_client,
    ):
        result = tool.run(api_key="NRAK-test", account_id="123456")
    assert result["available"] is False
    assert result["error_type"] == "timeout"


class TestMapQueryNewRelicAlerts:
    def test_records_entry_with_open_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_new_relic_alerts(
            evidence,
            {
                "available": True,
                "incidents": [
                    {"incident_id": "1", "status": "open", "priority": "CRITICAL"},
                    {"incident_id": "2", "status": "closed", "priority": "WARNING"},
                ],
                "total": 2,
                "truncated": False,
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "query_new_relic_alerts"
        assert entries[0]["summary"] == "2 incident(s), 1 open"

    def test_records_entry_for_all_closed_incidents_states_zero_open(self) -> None:
        """An all-closed result is a real, distinct finding ("nothing is still
        firing") -- the summary must state "0 open" explicitly, not omit the
        clause and leave it ambiguous whether open-ness was even checked."""
        evidence: dict[str, Any] = {}

        _map_query_new_relic_alerts(
            evidence,
            {
                "available": True,
                "incidents": [
                    {"incident_id": "1", "status": "closed"},
                    {"incident_id": "2", "status": "closed"},
                ],
                "truncated": False,
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "2 incident(s), 0 open"

    def test_records_entry_with_truncated_note(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_new_relic_alerts(
            evidence,
            {
                "available": True,
                "incidents": [{"incident_id": "1", "status": "closed"}],
                "truncated": True,
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "1 incident(s), 0 open (truncated)"

    def test_records_nothing_when_no_incidents(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_new_relic_alerts(
            evidence, {"available": True, "incidents": [], "total": 0, "truncated": False}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_new_relic_alerts(
            evidence, {"available": False, "error": "New Relic integration is not configured."}, {}
        )

        assert "catalog_entries" not in evidence
