"""Tests for MySQLReplicationStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.mysql.tools.mysql_replication_status_tool import (
    _map_get_mysql_replication_status,
    get_mysql_replication_status,
)
from tests.tools.conftest import BaseToolContract


class TestMySQLReplicationStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mysql_replication_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mysql_replication_status.__opensre_registered_tool__
    assert rt.name == "get_mysql_replication_status"
    assert rt.source == "mysql"


def test_run_happy_path_replica() -> None:
    fake_result = {
        "source": "mysql",
        "available": True,
        "is_replica": True,
        "replica_io_running": "Yes",
        "replica_sql_running": "Yes",
        "seconds_behind_source": 0,
        "source_host": "primary.mysql.example.com",
        "source_port": 3306,
        "last_error": "",
    }
    with patch(
        "integrations.mysql.tools.mysql_replication_status_tool.get_replication_status",
        return_value=fake_result,
    ):
        result = get_mysql_replication_status(host="replica.mysql.example.com", database="testdb")
    assert result["is_replica"] is True
    assert result["replica_io_running"] == "Yes"
    assert result["seconds_behind_source"] == 0


def test_run_happy_path_primary() -> None:
    fake_result = {
        "source": "mysql",
        "available": True,
        "is_replica": False,
        "note": "Server is not configured as a replica.",
    }
    with patch(
        "integrations.mysql.tools.mysql_replication_status_tool.get_replication_status",
        return_value=fake_result,
    ):
        result = get_mysql_replication_status(host="primary.mysql.example.com", database="testdb")
    assert result["is_replica"] is False
    assert "note" in result


def test_run_error_propagated() -> None:
    with patch(
        "integrations.mysql.tools.mysql_replication_status_tool.get_replication_status",
        return_value={"source": "mysql", "available": False, "error": "connection timed out"},
    ):
        result = get_mysql_replication_status(host="invalid", database="testdb")
    assert "error" in result
    assert result["available"] is False


class TestMapGetMysqlReplicationStatus:
    def test_records_entry_with_stalled_thread_and_lag(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mysql_replication_status(
            evidence,
            {
                "available": True,
                "replica_count": 1,
                "replicas": [
                    {
                        "Replica_IO_Running": "No",
                        "Replica_SQL_Running": "Yes",
                        "Seconds_Behind_Source": 120,
                    }
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_mysql_replication_status"
        assert entries[0]["summary"] == "1 replica(s), 1 with a stopped IO/SQL thread, max lag 120s"

    def test_records_entry_healthy_replica_without_clauses(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mysql_replication_status(
            evidence,
            {
                "available": True,
                "replica_count": 1,
                "replicas": [
                    {
                        "Replica_IO_Running": "Yes",
                        "Replica_SQL_Running": "Yes",
                        "Seconds_Behind_Source": 0,
                    }
                ],
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "1 replica(s), max lag 0s"

    def test_records_note_when_not_a_replica(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mysql_replication_status(
            evidence,
            {
                "available": True,
                "note": "This server is not configured as a replica.",
                "replicas": [],
            },
            {},
        )

        assert (
            evidence["catalog_entries"][0]["summary"]
            == "This server is not configured as a replica."
        )

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mysql_replication_status(evidence, {"available": False, "error": "timeout"}, {})

        assert "catalog_entries" not in evidence
