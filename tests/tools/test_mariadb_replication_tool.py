"""Tests for MariaDBReplicationTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.mariadb.tools.mariadb_replication_tool import (
    _map_get_mariadb_replication_status,
    get_mariadb_replication_status,
)
from tests.tools.conftest import BaseToolContract


class TestMariaDBReplicationToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mariadb_replication_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mariadb_replication_status.__opensre_registered_tool__
    assert rt.name == "get_mariadb_replication_status"
    assert rt.source == "mariadb"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mariadb",
        "available": True,
        "channels": [
            {"Slave_IO_Running": "Yes", "Slave_SQL_Running": "Yes", "Connection_name": ""},
        ],
    }
    with patch(
        "integrations.mariadb.tools.mariadb_replication_tool.get_replication_status",
        return_value=fake_result,
    ):
        result = get_mariadb_replication_status(host="localhost", database="test", username="user")
    assert result["available"] is True
    assert len(result["channels"]) == 1


def test_run_error_propagated() -> None:
    with patch(
        "integrations.mariadb.tools.mariadb_replication_tool.get_replication_status",
        return_value={"source": "mariadb", "available": False, "error": "connection timeout"},
    ):
        result = get_mariadb_replication_status(host="invalid", database="test", username="user")
    assert "error" in result


class TestMapGetMariadbReplicationStatus:
    def test_records_entry_with_stalled_thread_and_lag(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_replication_status(
            evidence,
            {
                "available": True,
                "channels": [
                    {
                        "Slave_IO_Running": "No",
                        "Slave_SQL_Running": "Yes",
                        "Seconds_Behind_Master": 90,
                    }
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_mariadb_replication_status"
        assert entries[0]["summary"] == "1 channel(s), 1 with a stopped IO/SQL thread, max lag 90s"

    def test_records_entry_healthy_channel_without_clauses(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_replication_status(
            evidence,
            {
                "available": True,
                "channels": [
                    {
                        "Slave_IO_Running": "Yes",
                        "Slave_SQL_Running": "Yes",
                        "Seconds_Behind_Master": 0,
                    }
                ],
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "1 channel(s), max lag 0s"

    def test_records_note_when_not_a_replica(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_replication_status(
            evidence,
            {
                "available": True,
                "note": "This server is not configured as a replica.",
                "channels": [],
            },
            {},
        )

        assert (
            evidence["catalog_entries"][0]["summary"]
            == "This server is not configured as a replica."
        )

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_replication_status(
            evidence, {"available": False, "error": "connection timeout"}, {}
        )

        assert "catalog_entries" not in evidence
