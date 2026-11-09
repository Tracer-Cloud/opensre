"""Evidence mapper coverage for the rds tools (#5572)."""

from __future__ import annotations

from integrations.rds.tools.rds_describe_instance_tool import _map_describe_rds_instance
from integrations.rds.tools.rds_events_tool import _map_describe_rds_events


class TestMapDescribeRdsEvents:
    def test_records_entry_when_events_present(self) -> None:
        evidence: dict = {}

        _map_describe_rds_events(
            evidence,
            {
                "available": True,
                "events": [
                    {
                        "date": "2026-08-24T10:00:00Z",
                        "message": "Multi-AZ failover started",
                        "categories": ["failover"],
                        "source_type": "db-instance",
                    }
                ],
                "total_events": 1,
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "describe_rds_events"
        assert "1" in entries[0]["summary"]

    def test_summary_says_reported_not_total_since_the_aws_call_is_unpaginated(self) -> None:
        """execute_aws_sdk_call makes one unpaginated call, so events can be a
        partial page — the summary must not imply it counted every event in
        the window."""
        evidence: dict = {}

        _map_describe_rds_events(
            evidence,
            {"available": True, "events": [{"message": "e"}] * 3, "duration_minutes": 60},
            {},
        )

        summary = evidence["catalog_entries"][0]["summary"]
        assert summary == "3 event(s) reported in the last 60 min"
        assert "lookback window" not in summary

    def test_records_nothing_when_no_events(self) -> None:
        evidence: dict = {}

        _map_describe_rds_events(evidence, {"available": True, "events": [], "total_events": 0}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict = {}

        _map_describe_rds_events(
            evidence, {"available": False, "error": "Failed to describe RDS events."}, {}
        )

        assert "catalog_entries" not in evidence


class TestMapDescribeRdsInstance:
    def test_records_entry_with_status_and_engine(self) -> None:
        evidence: dict = {}

        _map_describe_rds_instance(
            evidence,
            {
                "available": True,
                "status": "available",
                "engine": "postgres",
                "engine_version": "15.4",
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "describe_rds_instance"
        assert "available" in entries[0]["summary"]
        assert "postgres 15.4" in entries[0]["summary"]

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict = {}

        _map_describe_rds_instance(
            evidence,
            {"available": False, "error": "No RDS instance found with the given identifier."},
            {},
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_when_status_and_engine_both_missing(self) -> None:
        """available=True but neither field populated must not add a noise entry."""
        evidence: dict = {}

        _map_describe_rds_instance(
            evidence, {"available": True, "db_instance_identifier": "prod-db"}, {}
        )

        assert "catalog_entries" not in evidence
