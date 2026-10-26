"""Tests for MySQLServerStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.mysql.tools.mysql_server_status_tool import (
    _map_get_mysql_server_status,
    get_mysql_server_status,
)
from tests.tools.conftest import BaseToolContract


class TestMySQLServerStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mysql_server_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mysql_server_status.__opensre_registered_tool__
    assert rt.name == "get_mysql_server_status"
    assert rt.source == "mysql"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mysql",
        "available": True,
        "version": "8.0.32",
        "uptime_seconds": 432000,
        "connections": {
            "current": 25,
            "running": 5,
            "max_used": 50,
            "max_allowed": 151,
        },
        "queries": {
            "total": 1234567,
            "slow": 42,
            "per_second": 15.3,
        },
        "innodb": {
            "buffer_pool_size": 134217728,
            "buffer_pool_hit_ratio_percent": 98.7,
            "deadlocks": 0,
        },
    }
    with patch(
        "integrations.mysql.tools.mysql_server_status_tool.get_server_status",
        return_value=fake_result,
    ):
        result = get_mysql_server_status(host="localhost", database="testdb")
    assert result["version"] == "8.0.32"
    assert result["connections"]["current"] == 25
    assert result["innodb"]["buffer_pool_hit_ratio_percent"] == 98.7


def test_run_error_propagated() -> None:
    with patch(
        "integrations.mysql.tools.mysql_server_status_tool.get_server_status",
        return_value={"source": "mysql", "available": False, "error": "connection refused"},
    ):
        result = get_mysql_server_status(host="invalid", database="testdb")
    assert "error" in result
    assert result["available"] is False


class TestMapGetMysqlServerStatus:
    def test_records_entry_with_deadlocks(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mysql_server_status(
            evidence,
            {
                "available": True,
                "version": "8.0.32",
                "connections": {"current": 25, "max": 151},
                "innodb": {"deadlocks": 3},
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_mysql_server_status"
        assert entries[0]["summary"] == "MySQL 8.0.32, 25/151 connections, 3 InnoDB deadlock(s)"

    def test_records_entry_without_deadlock_clause_when_zero(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mysql_server_status(
            evidence,
            {
                "available": True,
                "version": "8.0.32",
                "connections": {"current": 5, "max": 151},
                "innodb": {"deadlocks": 0},
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "MySQL 8.0.32, 5/151 connections"

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mysql_server_status(evidence, {"available": False, "error": "timeout"}, {})

        assert "catalog_entries" not in evidence
