"""Tests for MariaDBReplicationTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MariaDBReplicationTool import get_mariadb_replication_status
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
    with patch("app.tools.MariaDBReplicationTool.get_replication_status", return_value=fake_result):
        result = get_mariadb_replication_status(host="localhost", database="test", username="user")
    assert result["available"] is True
    assert len(result["channels"]) == 1


def test_run_error_propagated() -> None:
    with patch("app.tools.MariaDBReplicationTool.get_replication_status", return_value={"source": "mariadb", "available": False, "error": "connection timeout"}):
        result = get_mariadb_replication_status(host="invalid", database="test", username="user")
    assert "error" in result
