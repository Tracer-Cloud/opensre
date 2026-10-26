"""Tests for MariaDBProcessListTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.mariadb.tools.mariadb_process_list_tool import (
    _map_get_mariadb_process_list,
    get_mariadb_process_list,
)
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
    with patch(
        "integrations.mariadb.tools.mariadb_process_list_tool.get_process_list",
        return_value=fake_result,
    ):
        result = get_mariadb_process_list(host="localhost", database="test", username="user")
    assert result["available"] is True
    assert result["total_processes"] == 1


def test_run_error_propagated() -> None:
    with patch(
        "integrations.mariadb.tools.mariadb_process_list_tool.get_process_list",
        return_value={"source": "mariadb", "available": False, "error": "connection timeout"},
    ):
        result = get_mariadb_process_list(host="invalid", database="test", username="user")
    assert "error" in result


def test_default_db_warning_present_when_database_omitted() -> None:
    with patch(
        "integrations.mariadb.tools.mariadb_process_list_tool.get_process_list",
        return_value={"source": "mariadb", "available": True, "processes": []},
    ):
        result = get_mariadb_process_list(host="localhost", username="user")
    assert "default_db_warning" in result
    assert "mysql" in result["default_db_warning"]


def test_no_default_db_warning_when_database_provided() -> None:
    with patch(
        "integrations.mariadb.tools.mariadb_process_list_tool.get_process_list",
        return_value={"source": "mariadb", "available": True, "processes": []},
    ):
        result = get_mariadb_process_list(host="localhost", username="user", database="mydb")
    assert "default_db_warning" not in result


class TestMapGetMariadbProcessList:
    def test_records_entry_with_longest_running(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_process_list(
            evidence,
            {
                "available": True,
                "total_processes": 2,
                "processes": [
                    {"id": 1, "time_secs": 15},
                    {"id": 2, "time_secs": 40},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_mariadb_process_list"
        assert entries[0]["summary"] == "2 active process(es) shown, longest running 40s"

    def test_records_nothing_when_no_processes(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_process_list(evidence, {"available": True, "processes": []}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_process_list(
            evidence, {"available": False, "error": "connection timeout"}, {}
        )

        assert "catalog_entries" not in evidence
