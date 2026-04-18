"""Tests for MySQLReplicationStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MySQLReplicationStatusTool import get_mysql_replication_status
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
    with patch("app.tools.MySQLReplicationStatusTool.get_replication_status", return_value=fake_result):
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
    with patch("app.tools.MySQLReplicationStatusTool.get_replication_status", return_value=fake_result):
        result = get_mysql_replication_status(host="primary.mysql.example.com", database="testdb")
    assert result["is_replica"] is False
    assert "note" in result


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.MySQLReplicationStatusTool.get_replication_status",
        return_value={"source": "mysql", "available": False, "error": "connection timed out"},
    ):
        result = get_mysql_replication_status(host="invalid", database="testdb")
    assert "error" in result
    assert result["available"] is False
