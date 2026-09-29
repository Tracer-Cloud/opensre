from __future__ import annotations

from typing import Any

from integrations.railway.tools.railway_deployment_tool.inspect_tool import (
    _map_inspect_railway_deployment,
    inspect_railway_deployment,
)


def test_inspect_railway_deployment_has_a_service_level_name() -> None:
    assert inspect_railway_deployment.name == "inspect_railway_deployment"
    assert "Slack bot" not in inspect_railway_deployment.description


def test_inspect_railway_deployment_carries_mapper() -> None:
    rt = inspect_railway_deployment.__opensre_registered_tool__
    assert rt.evidence_mapper is _map_inspect_railway_deployment


class TestMapInspectRailwayDeployment:
    def test_records_entry_with_commit(self) -> None:
        evidence: dict[str, Any] = {}

        _map_inspect_railway_deployment(
            evidence,
            {
                "status": "ok",
                "deployment": {
                    "status": "SUCCESS",
                    "commit_hash": "abcdef1234567890",
                    "commit_message": "Fix connection pool leak",
                },
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "inspect_railway_deployment"
        assert (
            entries[0]["summary"]
            == "status SUCCESS, commit abcdef123456, 'Fix connection pool leak'"
        )

    def test_records_entry_without_optional_clauses(self) -> None:
        evidence: dict[str, Any] = {}

        _map_inspect_railway_deployment(
            evidence, {"status": "ok", "deployment": {"status": "SUCCESS"}}, {}
        )

        assert evidence["catalog_entries"][0]["summary"] == "status SUCCESS"

    def test_strips_carriage_returns_from_commit_message(self) -> None:
        """Regression: a commit message with bare \\r or \\r\\n line endings
        must not leave a literal carriage return in the report summary."""
        evidence: dict[str, Any] = {}

        _map_inspect_railway_deployment(
            evidence,
            {
                "status": "ok",
                "deployment": {"status": "SUCCESS", "commit_message": "Fix bug\r\nline two\r"},
            },
            {},
        )

        assert "\r" not in evidence["catalog_entries"][0]["summary"]

    def test_records_nothing_when_deployment_empty(self) -> None:
        evidence: dict[str, Any] = {}

        _map_inspect_railway_deployment(evidence, {"status": "ok", "deployment": {}}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_failed_status(self) -> None:
        evidence: dict[str, Any] = {}

        _map_inspect_railway_deployment(evidence, {"status": "failed", "error": "not found"}, {})

        assert "catalog_entries" not in evidence
