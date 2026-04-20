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


def test_run_happy_path_explicit_source() -> None:
    fake = {
        "source": "betterstack",
        "available": True,
        "betterstack_source": "t1_myapp",
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
            sources=["t1_myapp", "t2_gateway"],
            source="t1_myapp",
        )
    assert result["available"] is True
    assert result["row_count"] == 1
    # Source explicitly chosen — the first positional arg to query_logs after config.
    args, _kwargs = mock_query.call_args
    assert args[1] == "t1_myapp"


def test_source_falls_back_to_first_configured() -> None:
    with patch(
        "app.tools.BetterStackLogsTool.query_logs",
        return_value={
            "source": "betterstack",
            "available": True,
            "betterstack_source": "t1_x",
            "rows": [],
            "row_count": 0,
        },
    ) as mock_query:
        query_betterstack_logs(
            query_endpoint="https://x",
            username="u",
            password="p",
            sources=["t1_x", "t2_y"],
        )
    args, _kwargs = mock_query.call_args
    assert args[1] == "t1_x"


def test_missing_source_and_no_hints_surfaces_downstream() -> None:
    # When neither source nor sources are provided, query_logs is still called
    # with an empty source — downstream validation returns the structured error.
    with patch(
        "app.tools.BetterStackLogsTool.query_logs",
        return_value={
            "source": "betterstack",
            "available": False,
            "error": "Invalid Better Stack source identifier: ''.",
            "betterstack_source": "",
            "rows": [],
            "row_count": 0,
        },
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
