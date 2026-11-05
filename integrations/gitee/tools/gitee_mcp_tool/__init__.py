"""Gitee MCP bridge tools."""

from __future__ import annotations

from integrations.gitee.tools.gitee_mcp_tool.tool import call_gitee_tool, list_gitee_tools

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "call_gitee_tool", "list_gitee_tools"]
