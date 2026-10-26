"""Tests for MariaDBStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.mariadb.tools.mariadb_status_tool import (
    _map_get_mariadb_global_status,
    get_mariadb_global_status,
)
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
    with patch(
        "integrations.mariadb.tools.mariadb_status_tool.get_global_status", return_value=fake_result
    ):
        result = get_mariadb_global_status(host="localhost", database="test", username="user")
    assert result["available"] is True
    assert "Threads_connected" in result["metrics"]


def test_run_error_propagated() -> None:
    with patch(
        "integrations.mariadb.tools.mariadb_status_tool.get_global_status",
        return_value={"source": "mariadb", "available": False, "error": "connection timeout"},
    ):
        result = get_mariadb_global_status(host="invalid", database="test", username="user")
    assert "error" in result


class TestMapGetMariadbGlobalStatus:
    def test_records_entry_with_deadlocks(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_global_status(
            evidence,
            {
                "available": True,
                "metrics": {
                    "Threads_connected": "10",
                    "Uptime": "86400",
                    "Innodb_deadlocks": "2",
                },
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_mariadb_global_status"
        assert (
            entries[0]["summary"] == "uptime 86400s, 10 thread(s) connected, 2 InnoDB deadlock(s)"
        )

    def test_records_entry_without_deadlock_clause_when_zero(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_global_status(
            evidence,
            {
                "available": True,
                "metrics": {
                    "Threads_connected": "5",
                    "Uptime": "3600",
                    "Innodb_deadlocks": "0",
                },
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "uptime 3600s, 5 thread(s) connected"

    def test_records_nothing_when_no_metrics(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_global_status(evidence, {"available": True, "metrics": {}}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_global_status(
            evidence, {"available": False, "error": "connection timeout"}, {}
        )

        assert "catalog_entries" not in evidence
