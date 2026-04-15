"""Tests for MariaDBStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MariaDBStatusTool import get_mariadb_global_status
from tests.tools.conftest import BaseToolContract


class TestMariaDBStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mariadb_global_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mariadb_global_status.__opensre_registered_tool__
    assert rt.name == "get_mariadb_global_status"
    assert rt.source == "mariadb"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mariadb",
        "available": True,
        "metrics": {"Threads_connected": "10", "Uptime": "86400"},
    }
    with patch("app.tools.MariaDBStatusTool.get_global_status", return_value=fake_result):
        result = get_mariadb_global_status(host="localhost", database="test", username="user")
    assert result["available"] is True
    assert "Threads_connected" in result["metrics"]


def test_run_error_propagated() -> None:
    with patch("app.tools.MariaDBStatusTool.get_global_status", return_value={"source": "mariadb", "available": False, "error": "connection timeout"}):
        result = get_mariadb_global_status(host="invalid", database="test", username="user")
    assert "error" in result
