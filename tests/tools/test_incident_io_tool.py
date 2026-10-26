from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from integrations.incident_io.tools import IncidentIoIncidentsTool, _map_incident_io_incidents


def test_incident_io_tool_extracts_credentials_from_sources() -> None:
    tool = IncidentIoIncidentsTool()

    params = tool.extract_params(
        {
            "incident_io": {
                "api_key": "secret",
                "base_url": "https://api.incident.io",
                "incident_id": "inc-123",
            }
        }
    )

    assert params["api_key"] == "secret"
    assert params["action"] == "context"
    assert params["incident_id"] == "inc-123"
    assert tool.input_schema["required"] == []


def test_incident_io_tool_runs_context(monkeypatch) -> None:
    tool = IncidentIoIncidentsTool()
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    client.get_incident_context.return_value = {
        "success": True,
        "incident": {"id": "inc-123"},
        "incident_updates": [],
    }

    monkeypatch.setattr(
        "integrations.incident_io.tools.make_incident_io_client",
        lambda *_args, **_kwargs: client,
    )

    result = tool.run(api_key="secret", action="context", incident_id="inc-123")

    assert result["success"] is True
    assert result["source"] == "incident_io"
    client.get_incident_context.assert_called_once_with("inc-123", update_limit=20)


def test_incident_io_tool_runs_append_summary(monkeypatch) -> None:
    tool = IncidentIoIncidentsTool()
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    client.append_summary_update.return_value = {"success": True}

    monkeypatch.setattr(
        "integrations.incident_io.tools.make_incident_io_client",
        lambda *_args, **_kwargs: client,
    )

    result = tool.run(
        api_key="secret",
        action="append_summary",
        incident_id="inc-123",
        title="RCA",
        body="Finding",
        notify_incident_channel=True,
    )

    assert result["success"] is True
    client.append_summary_update.assert_called_once_with(
        "inc-123",
        title="RCA",
        body="Finding",
        notify_incident_channel=True,
    )


def test_incident_io_tool_requires_incident_id_for_context(monkeypatch) -> None:
    tool = IncidentIoIncidentsTool()
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None

    monkeypatch.setattr(
        "integrations.incident_io.tools.make_incident_io_client",
        lambda *_args, **_kwargs: client,
    )

    result = tool.run(api_key="secret", action="context")

    assert result["success"] is False
    assert "incident_id" in result["error"]


class TestMapIncidentIoIncidents:
    def test_records_entry_for_list_action(self) -> None:
        evidence: dict[str, Any] = {}

        _map_incident_io_incidents(
            evidence,
            {
                "available": True,
                "action": "list",
                "total": 2,
                "incidents": [{"id": "i1"}, {"id": "i2"}],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "incident_io_incidents"
        assert entries[0]["summary"] == "2 incident(s)"

    def test_qualifies_list_count_when_more_pages_exist(self) -> None:
        evidence: dict[str, Any] = {}

        _map_incident_io_incidents(
            evidence,
            {
                "available": True,
                "action": "list",
                "total": 20,
                "incidents": [{"id": "i1"}],
                "pagination_meta": {"after": "cursor-1"},
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "20+ incident(s)"

    def test_records_entry_for_get_action(self) -> None:
        evidence: dict[str, Any] = {}

        _map_incident_io_incidents(
            evidence,
            {
                "available": True,
                "action": "get",
                "incident": {"name": "Database outage", "status": "Mitigating", "severity": "SEV1"},
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert entries[0]["summary"] == "'Database outage', Mitigating, SEV1"

    def test_strips_carriage_returns_from_incident_name(self) -> None:
        """Regression: a name with bare \\r or \\r\\n line endings must not
        leave a literal carriage return in the report summary."""
        evidence: dict[str, Any] = {}

        _map_incident_io_incidents(
            evidence,
            {
                "available": True,
                "action": "get",
                "incident": {"name": "Database\r\noutage\r", "status": "Mitigating"},
            },
            {},
        )

        assert "\r" not in evidence["catalog_entries"][0]["summary"]

    def test_records_entry_for_updates_action(self) -> None:
        evidence: dict[str, Any] = {}

        _map_incident_io_incidents(
            evidence,
            {
                "available": True,
                "action": "updates",
                "total": 3,
                "incident_updates": [{"id": "u1"}],
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "3 update(s)"

    def test_records_entry_for_context_action_with_update_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_incident_io_incidents(
            evidence,
            {
                "available": True,
                "action": "context",
                "incident": {"name": "Database outage", "status": "Mitigating"},
                "total_updates": 5,
            },
            {},
        )

        assert (
            evidence["catalog_entries"][0]["summary"]
            == "'Database outage', Mitigating, 5 update(s)"
        )

    def test_qualifies_context_update_count_when_more_pages_exist(self) -> None:
        """Regression: get_incident_context's total_updates can be a single
        page -- when pagination_meta.after is present, cite it as a floor."""
        evidence: dict[str, Any] = {}

        _map_incident_io_incidents(
            evidence,
            {
                "available": True,
                "action": "context",
                "incident": {"name": "Database outage", "status": "Mitigating"},
                "total_updates": 20,
                "pagination_meta": {"after": "cursor-1"},
            },
            {},
        )

        assert (
            evidence["catalog_entries"][0]["summary"]
            == "'Database outage', Mitigating, 20+ update(s)"
        )

    def test_records_nothing_for_append_summary_action(self) -> None:
        evidence: dict[str, Any] = {}

        _map_incident_io_incidents(
            evidence, {"available": True, "action": "append_summary", "success": True}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_when_list_is_empty(self) -> None:
        evidence: dict[str, Any] = {}

        _map_incident_io_incidents(
            evidence, {"available": True, "action": "list", "total": 0, "incidents": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_incident_io_incidents(evidence, {"available": False, "error": "HTTP 401"}, {})

        assert "catalog_entries" not in evidence
