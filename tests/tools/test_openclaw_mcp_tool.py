"""Tests for OpenClaw MCP function tools."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.OpenClawMCPTool import (
    call_openclaw_bridge_tool,
    list_openclaw_bridge_tools,
    search_openclaw_conversations,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestOpenClawListToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_openclaw_bridge_tools.__opensre_registered_tool__


class TestOpenClawCallToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return call_openclaw_bridge_tool.__opensre_registered_tool__


class TestOpenClawConversationSearchToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return search_openclaw_conversations.__opensre_registered_tool__


def test_openclaw_tools_are_available_from_agent_state() -> None:
    sources = mock_agent_state({
        "openclaw": {
            "connection_verified": True,
            "openclaw_mode": "stdio",
            "openclaw_command": "openclaw",
            "openclaw_args": ["mcp", "serve"],
            "openclaw_search_query": "checkout-api",
        }
    })

    assert list_openclaw_bridge_tools.__opensre_registered_tool__.is_available(sources) is True
    assert call_openclaw_bridge_tool.__opensre_registered_tool__.is_available(sources) is True
    assert search_openclaw_conversations.__opensre_registered_tool__.is_available(sources) is True


def test_extract_params_maps_openclaw_source_fields() -> None:
    rt = call_openclaw_bridge_tool.__opensre_registered_tool__
    params = rt.extract_params(
        mock_agent_state({
            "openclaw": {
                "connection_verified": True,
                "openclaw_mode": "stdio",
                "openclaw_command": "openclaw",
                "openclaw_args": ["mcp", "serve"],
                "openclaw_token": "",
                "openclaw_search_query": "checkout-api",
            }
        })
    )

    assert params["openclaw_mode"] == "stdio"
    assert params["openclaw_command"] == "openclaw"
    assert params["openclaw_args"] == ["mcp", "serve"]


def test_search_extract_params_maps_query() -> None:
    rt = search_openclaw_conversations.__opensre_registered_tool__
    params = rt.extract_params(
        mock_agent_state({
            "openclaw": {
                "connection_verified": True,
                "openclaw_mode": "stdio",
                "openclaw_command": "openclaw",
                "openclaw_args": ["mcp", "serve"],
                "openclaw_search_query": "checkout-api",
            }
        })
    )

    assert params["search"] == "checkout-api"
    assert params["limit"] == 10


def test_list_openclaw_tools_returns_unavailable_without_config() -> None:
    with patch("app.tools.OpenClawMCPTool.openclaw_config_from_env", return_value=None):
        result = list_openclaw_bridge_tools()

    assert result["available"] is False
    assert result["tools"] == []


def test_list_openclaw_tools_happy_path() -> None:
    mock_config = MagicMock()
    mock_config.mode = "stdio"
    mock_config.command = "openclaw"
    mock_config.url = ""

    with patch("app.tools.OpenClawMCPTool.openclaw_config_from_env", return_value=None), \
         patch("app.tools.OpenClawMCPTool.build_openclaw_config", return_value=mock_config), \
         patch(
             "app.tools.OpenClawMCPTool.list_openclaw_mcp_tools",
             return_value=[{"name": "messages_read", "description": "", "input_schema": {}}],
         ):
        result = list_openclaw_bridge_tools(
            openclaw_mode="stdio",
            openclaw_command="openclaw",
            openclaw_args=["mcp", "serve"],
        )

    assert result["available"] is True
    assert result["transport"] == "stdio"
    assert result["tools"][0]["name"] == "messages_read"


def test_call_openclaw_tool_happy_path() -> None:
    mock_config = MagicMock()

    with patch("app.tools.OpenClawMCPTool.openclaw_config_from_env", return_value=None), \
         patch("app.tools.OpenClawMCPTool.build_openclaw_config", return_value=mock_config), \
         patch(
             "app.tools.OpenClawMCPTool.invoke_openclaw_mcp_tool",
             return_value={
                 "is_error": False,
                 "tool": "messages_read",
                 "arguments": {"session_key": "abc"},
                 "text": "ok",
                 "structured_content": [{"id": "msg-1"}],
                 "content": [],
             },
         ):
        result = call_openclaw_bridge_tool(
            tool_name="messages_read",
            arguments={"session_key": "abc"},
            openclaw_mode="stdio",
            openclaw_command="openclaw",
            openclaw_args=["mcp", "serve"],
        )

    assert result["available"] is True
    assert result["tool"] == "messages_read"
    assert result["structured_content"] == [{"id": "msg-1"}]


def test_call_openclaw_tool_returns_error_payload() -> None:
    mock_config = MagicMock()

    with patch("app.tools.OpenClawMCPTool.openclaw_config_from_env", return_value=None), \
         patch("app.tools.OpenClawMCPTool.build_openclaw_config", return_value=mock_config), \
         patch(
             "app.tools.OpenClawMCPTool.invoke_openclaw_mcp_tool",
             return_value={
                 "is_error": True,
                 "tool": "messages_send",
                 "arguments": {"session_key": "abc"},
                 "text": "route missing",
             },
         ):
        result = call_openclaw_bridge_tool(
            tool_name="messages_send",
            arguments={"session_key": "abc"},
            openclaw_mode="stdio",
            openclaw_command="openclaw",
            openclaw_args=["mcp", "serve"],
        )

    assert result["available"] is False
    assert "route missing" in result["error"]


def test_search_openclaw_conversations_happy_path() -> None:
    mock_config = MagicMock()

    with patch("app.tools.OpenClawMCPTool.openclaw_config_from_env", return_value=None), \
         patch("app.tools.OpenClawMCPTool.build_openclaw_config", return_value=mock_config), \
         patch(
             "app.tools.OpenClawMCPTool.invoke_openclaw_mcp_tool",
             return_value={
                 "is_error": False,
                 "tool": "conversations_list",
                 "arguments": {"search": "checkout-api", "limit": 10},
                 "text": "1 conversation",
                 "structured_content": [{"session_key": "sess-1", "title": "Checkout debugging"}],
                 "content": [],
             },
         ):
        result = search_openclaw_conversations(
            search="checkout-api",
            openclaw_mode="stdio",
            openclaw_command="openclaw",
            openclaw_args=["mcp", "serve"],
        )

    assert result["available"] is True
    assert result["conversations"] == [{"session_key": "sess-1", "title": "Checkout debugging"}]
