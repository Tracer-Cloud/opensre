"""Tests for MySQLCurrentProcessesTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from app.tools.MySQLCurrentProcessesTool import get_mysql_current_processes
from tests.tools.conftest import BaseToolContract


class TestMySQLCurrentProcessesToolContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return get_mysql_current_processes.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mysql_current_processes.__opensre_registered_tool__
    assert rt.name == "get_mysql_current_processes"
    assert rt.source == "mysql"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mysql",
        "available": True,
        "threshold_seconds": 2,
        "total_processes": 2,
        "processes": [
            {
                "id": 42,
                "user": "app_user",
                "host": "192.168.1.100:54321",
                "db": "application_db",
                "command": "Query",
                "time": 15,
                "state": "Sending data",
                "info": "SELECT * FROM large_table WHERE status = 'pending'",
            },
            {
                "id": 43,
                "user": "analytics",
                "host": "10.0.0.5:11223",
                "db": "application_db",
                "command": "Query",
                "time": 30,
                "state": "Sorting result",
                "info": "SELECT COUNT(*) FROM events GROUP BY user_id",
            },
        ],
    }
    with patch("app.tools.MySQLCurrentProcessesTool.get_current_processes", return_value=fake_result):
        result = get_mysql_current_processes(host="localhost", database="testdb", threshold_seconds=2)
    assert result["threshold_seconds"] == 2
    assert result["total_processes"] == 2
    assert len(result["processes"]) == 2
    assert result["processes"][0]["time"] == 15


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.MySQLCurrentProcessesTool.get_current_processes",
        return_value={"source": "mysql", "available": False, "error": "access denied"},
    ):
        result = get_mysql_current_processes(host="invalid", database="testdb")
    assert "error" in result
    assert result["available"] is False
