"""Tests for MariaDBProcessListTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MariaDBProcessListTool import get_mariadb_process_list
from tests.tools.conftest import BaseToolContract


class TestMariaDBProcessListToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mariadb_process_list.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mariadb_process_list.__opensre_registered_tool__
    assert rt.name == "get_mariadb_process_list"
    assert rt.source == "mariadb"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mariadb",
        "available": True,
        "total_processes": 1,
        "processes": [{"id": 1, "user": "root", "command": "Query", "query": "SELECT 1"}],
    }
    with patch("app.tools.MariaDBProcessListTool.get_process_list", return_value=fake_result):
        result = get_mariadb_process_list(host="localhost", database="test", username="user")
    assert result["available"] is True
    assert result["total_processes"] == 1


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.MariaDBProcessListTool.get_process_list",
        return_value={"source": "mariadb", "available": False, "error": "connection timeout"},
    ):
        result = get_mariadb_process_list(host="invalid", database="test", username="user")
    assert "error" in result
