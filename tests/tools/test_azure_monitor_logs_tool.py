"""Tests for AzureMonitorLogsTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from app.tools.AzureMonitorLogsTool import (
    _bounded_limit,
    _ensure_take_clause,
    query_azure_monitor_logs,
)
from tests.tools.conftest import BaseToolContract


def _registered_tool() -> Any:
    return cast(Any, query_azure_monitor_logs).__opensre_registered_tool__


class TestAzureMonitorLogsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool()


@pytest.mark.parametrize(
    "sources,expected",
    [
        (
            {
                "azure": {
                    "connection_verified": True,
                    "workspace_id": "workspace-123",
                    "access_token": "token-abc",
                }
            },
            True,
        ),
        (
            {
                "azure": {
                    "connection_verified": False,
                    "workspace_id": "workspace-123",
                    "access_token": "token-abc",
                }
            },
            False,
        ),
        (
            {
                "azure": {
                    "connection_verified": True,
                    "workspace_id": "",
                    "access_token": "token-abc",
                }
            },
            False,
        ),
        (
            {
                "azure": {
                    "connection_verified": True,
                    "workspace_id": "workspace-123",
                    "access_token": "",
                }
            },
            False,
        ),
        ({}, False),
    ],
)
def test_is_available_requires_verified_workspace_and_token(sources: dict, expected: bool) -> None:
    rt = _registered_tool()
    assert rt.is_available(sources) is expected


def test_extract_params_maps_fields_and_defaults() -> None:
    rt = _registered_tool()
    params = rt.extract_params(
        {
            "azure": {
                "workspace_id": " workspace-123 ",
                "access_token": " token-abc ",
                "endpoint": " https://api.loganalytics.io ",
            }
        }
    )

    assert params["workspace_id"] == "workspace-123"
    assert params["access_token"] == "token-abc"
    assert params["endpoint"] == "https://api.loganalytics.io"
    assert params["time_range_minutes"] == 60
    assert params["limit"] == 50


def test_bounded_limit_caps_requested_limit() -> None:
    assert _bounded_limit(300, 100) == 100


def test_bounded_limit_enforces_hard_ceiling() -> None:
    # max_results above _MAX_HARD_LIMIT (200) must still be capped at 200
    assert _bounded_limit(500, 300) == 200


def test_bounded_limit_enforces_minimum_of_one() -> None:
    assert _bounded_limit(0, 100) == 1
    assert _bounded_limit(-10, 100) == 1


@pytest.mark.parametrize(
    "query,limit,expected",
    [
        ("", 10, "AppTraces | order by TimeGenerated desc | take 10"),
        (
            "AppTraces | order by TimeGenerated desc",
            5,
            "AppTraces | order by TimeGenerated desc | take 5",
        ),
        ("AppTraces | take 100", 5, "AppTraces | take 100"),
    ],
)
def test_ensure_take_clause_branches(query: str, limit: int, expected: str) -> None:
    assert _ensure_take_clause(query, limit) == expected


def test_run_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    mocked_response = MagicMock()
    mocked_response.raise_for_status.return_value = None
    mocked_response.json.return_value = {
        "tables": [
            {
                "columns": [
                    {"name": "TimeGenerated"},
                    {"name": "Message"},
                ],
                "rows": [
                    ["2026-04-27T10:00:00Z", "error: failed to connect"],
                    ["2026-04-27T10:01:00Z", "info: retry succeeded"],
                ],
            }
        ]
    }

    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> MagicMock:
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        captured["json"] = kwargs.get("json", {})
        return mocked_response

    monkeypatch.setattr("app.tools.AzureMonitorLogsTool.httpx.post", fake_post)

    result = query_azure_monitor_logs(
        workspace_id="workspace-123",
        access_token="token-abc",
        query="AppTraces | order by TimeGenerated desc",
        limit=2,
    )

    assert result["available"] is True
    assert result["source"] == "azure"
    assert result["total_returned"] == 2
    assert result["rows"][0]["Message"] == "error: failed to connect"
    # Assert the outgoing request was constructed correctly
    assert "workspace-123" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer token-abc"
    assert "query" in captured["json"]


def test_run_http_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    mocked_response = MagicMock()
    mocked_response.raise_for_status.side_effect = Exception("401 Client Error: Unauthorized")
    mocked_response.json.return_value = {}

    monkeypatch.setattr(
        "app.tools.AzureMonitorLogsTool.httpx.post",
        lambda *_args, **_kwargs: mocked_response,
    )

    result = query_azure_monitor_logs(
        workspace_id="workspace-123",
        access_token="token-abc",
        query="AppTraces",
    )

    assert "error" in result
    assert "401" in result["error"]
    assert result["source"] == "azure"
    assert result["available"] is False
    assert result["rows"] == []


def test_run_unavailable_without_credentials() -> None:
    result = query_azure_monitor_logs(workspace_id="", access_token="", query="AppTraces")

    assert result["available"] is False
    assert "missing azure credentials" in result["error"].lower()
