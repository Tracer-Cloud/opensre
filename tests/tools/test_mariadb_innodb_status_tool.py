"""Tests for MariaDBInnoDBStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.mariadb.tools.mariadb_innodb_status_tool import (
    _map_get_mariadb_innodb_status,
    get_mariadb_innodb_status,
)
from tests.tools.conftest import BaseToolContract


class TestMariaDBInnoDBStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mariadb_innodb_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mariadb_innodb_status.__opensre_registered_tool__
    assert rt.name == "get_mariadb_innodb_status"
    assert rt.source == "mariadb"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mariadb",
        "available": True,
        "innodb_status": "=====================================\nBUFFER POOL AND MEMORY\n=====================================",
    }
    with patch(
        "integrations.mariadb.tools.mariadb_innodb_status_tool.get_innodb_status",
        return_value=fake_result,
    ):
        result = get_mariadb_innodb_status(host="localhost", database="test", username="user")
    assert result["available"] is True
    assert "innodb_status" in result


def test_run_error_propagated() -> None:
    with patch(
        "integrations.mariadb.tools.mariadb_innodb_status_tool.get_innodb_status",
        return_value={"source": "mariadb", "available": False, "error": "connection timeout"},
    ):
        result = get_mariadb_innodb_status(host="invalid", database="test", username="user")
    assert "error" in result


class TestMapGetMariadbInnodbStatus:
    def test_records_entry_flagging_deadlock_section(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_innodb_status(
            evidence,
            {
                "available": True,
                "innodb_status": "...\nLATEST DETECTED DEADLOCK\n...",
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_mariadb_innodb_status"
        assert (
            entries[0]["summary"] == "InnoDB engine status captured — includes a recorded deadlock"
        )

    def test_records_entry_without_deadlock_clause(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_innodb_status(
            evidence,
            {"available": True, "innodb_status": "BUFFER POOL AND MEMORY\n..."},
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "InnoDB engine status captured"

    def test_records_nothing_when_status_text_empty(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_innodb_status(evidence, {"available": True, "innodb_status": ""}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_innodb_status(
            evidence, {"available": False, "error": "connection timeout"}, {}
        )

        assert "catalog_entries" not in evidence
