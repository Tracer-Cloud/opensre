"""Tests for PostgreSQLReplicationStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.PostgreSQLReplicationStatusTool import get_postgresql_replication_status
from tests.tools.conftest import BaseToolContract


class TestPostgreSQLReplicationStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_postgresql_replication_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_postgresql_replication_status.__opensre_registered_tool__
    assert rt.name == "get_postgresql_replication_status"
    assert rt.source == "postgresql"


def test_run_happy_path_with_replicas() -> None:
    fake_result = {
        "source": "postgresql",
        "available": True,
        "is_primary": True,
        "current_wal_lsn": "1/A2B3C4D5",
        "replica_count": 2,
        "replicas": [
            {
                "pid": 23456,
                "username": "replica_user",
                "application_name": "replica_1",
                "client_addr": "10.0.1.100",
                "client_hostname": "replica-1.internal",
                "state": "streaming",
                "sent_lsn": "1/A2B3C4D5",
                "write_lsn": "1/A2B3C4D4",
                "flush_lsn": "1/A2B3C4D3",
                "replay_lsn": "1/A2B3C4D3",
                "write_lag": "00:00:00.001",
                "flush_lag": "00:00:00.002",
                "replay_lag": "00:00:00.003",
                "sync_state": "async",
            },
            {
                "pid": 23457,
                "username": "replica_user",
                "application_name": "replica_2",
                "client_addr": "10.0.1.101",
                "client_hostname": "replica-2.internal",
                "state": "streaming",
                "sent_lsn": "1/A2B3C4D5",
                "write_lsn": "1/A2B3C4D5",
                "flush_lsn": "1/A2B3C4D5",
                "replay_lsn": "1/A2B3C4D5",
                "write_lag": "",
                "flush_lag": "",
                "replay_lag": "",
                "sync_state": "sync",
            },
        ],
    }
    with patch(
        "app.tools.PostgreSQLReplicationStatusTool.get_replication_status", return_value=fake_result
    ):
        result = get_postgresql_replication_status(host="localhost", database="testdb")
    assert result["is_primary"] is True
    assert result["replica_count"] == 2
    assert result["current_wal_lsn"] == "1/A2B3C4D5"
    assert len(result["replicas"]) == 2
    assert result["replicas"][1]["sync_state"] == "sync"


def test_run_happy_path_no_replicas() -> None:
    fake_result = {
        "source": "postgresql",
        "available": True,
        "is_primary": True,
        "current_wal_lsn": "1/A2B3C4D5",
        "replicas": [],
        "note": "Server is a primary but has no active replicas.",
    }
    with patch(
        "app.tools.PostgreSQLReplicationStatusTool.get_replication_status", return_value=fake_result
    ):
        result = get_postgresql_replication_status(host="localhost", database="testdb")
    assert result["is_primary"] is True
    assert len(result["replicas"]) == 0
    assert "note" in result


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.PostgreSQLReplicationStatusTool.get_replication_status",
        return_value={"source": "postgresql", "available": False, "error": "access denied"},
    ):
        result = get_postgresql_replication_status(host="invalid", database="testdb")
    assert "error" in result
    assert result["available"] is False
