"""Registry entrypoint for the Neon project status tool."""

from __future__ import annotations

from integrations.neon.tools.neon_project_status_tool.tool import (
    NeonProjectStatusTool,
    get_neon_project_status,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "NeonProjectStatusTool", "get_neon_project_status"]
