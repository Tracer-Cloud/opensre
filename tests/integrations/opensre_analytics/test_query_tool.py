"""The query tool reads aggregate reports only, never customers' conversations."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.opensre_analytics.config import OpenSREAnalyticsConfig
from integrations.opensre_analytics.tools.opensre_analytics_query_tool import tool


def _all_reports(_config: OpenSREAnalyticsConfig) -> list[dict[str, object]]:
    return [
        {"name": "product_overview", "description": "Home page summary", "input_schema": {}},
        {"name": "user_conversation", "description": "Prompts and responses", "input_schema": {}},
        {"name": "priority_diagnosis_skill", "description": "Instructions", "input_schema": {}},
    ]


def _unexpected_call(*_args: Any, **_kwargs: Any) -> dict[str, object]:
    raise AssertionError("a withheld report must not be requested from the analytics app")


def test_conversation_reports_are_refused_without_calling_the_app_and_hidden_from_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr(tool, "list_tools", _all_reports)
    monkeypatch.setattr(tool, "call_tool", _unexpected_call)

    # Act
    refused = tool.query_opensre_analytics(
        tool_name="user_conversation", arguments={"userId": "u1"}, analytics_token="token"
    )
    listing = tool.list_opensre_analytics_tools(analytics_token="token")

    # Assert
    assert refused["available"] is False
    assert "analytics dashboard" in str(refused["error"])
    entries = listing["tools"]
    assert isinstance(entries, list)
    assert {str(entry["name"]) for entry in entries} == {"product_overview"}
