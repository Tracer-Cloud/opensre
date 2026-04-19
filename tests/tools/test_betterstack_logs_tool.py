"""Tests for BetterStackLogsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.BetterStackLogsTool import query_betterstack_logs
from tests.tools.conftest import BaseToolContract


class TestBetterStackLogsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return query_betterstack_logs.__opensre_registered_tool__


def test_metadata() -> None:
    rt = query_betterstack_logs.__opensre_registered_tool__
    assert rt.name == "query_betterstack_logs"
    assert rt.source == "betterstack"
    assert "investigation" in rt.surfaces


def test_run_happy_path_explicit_table() -> None:
    fake = {
        "source": "betterstack",
        "available": True,
        "table": "t1_myapp_logs",
        "rows": [{"dt": "2026-04-20T00:00:00Z", "raw": "hello"}],
        "row_count": 1,
    }
    with patch(
        "app.tools.BetterStackLogsTool.query_logs",
        return_value=fake,
    ) as mock_query:
        result = query_betterstack_logs(
            query_endpoint="https://eu-nbg-2-connect.betterstackdata.com",
            username="u",
            password="p",
            tables=["t1_myapp_logs", "t2_gateway_logs"],
            table="t1_myapp_logs",
        )
    assert result["available"] is True
    assert result["row_count"] == 1
    # Table explicitly chosen — the first positional arg to query_logs after config.
    args, _kwargs = mock_query.call_args
    assert args[1] == "t1_myapp_logs"


def test_table_falls_back_to_first_configured() -> None:
    with patch(
        "app.tools.BetterStackLogsTool.query_logs",
        return_value={"source": "betterstack", "available": True, "table": "t1_x_logs", "rows": [], "row_count": 0},
    ) as mock_query:
        query_betterstack_logs(
            query_endpoint="https://x",
            username="u",
            password="p",
            tables=["t1_x_logs", "t2_y_logs"],
        )
    args, _kwargs = mock_query.call_args
    assert args[1] == "t1_x_logs"


def test_missing_table_and_no_hints_surfaces_downstream() -> None:
    # When neither table nor tables are provided, query_logs is still called
    # with an empty table — downstream validation returns the structured error.
    with patch(
        "app.tools.BetterStackLogsTool.query_logs",
        return_value={"source": "betterstack", "available": False, "error": "Invalid Better Stack table name: ''.", "rows": [], "row_count": 0, "table": ""},
    ) as mock_query:
        result = query_betterstack_logs(
            query_endpoint="https://x",
            username="u",
            password="p",
        )
    args, _kwargs = mock_query.call_args
    assert args[1] == ""
    assert result["available"] is False
    assert "invalid" in result["error"].lower()
