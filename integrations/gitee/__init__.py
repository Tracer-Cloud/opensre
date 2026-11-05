"""Gitee MCP integration public surface."""

from __future__ import annotations

from config.constants.gitee import (
    DEFAULT_GITEE_MCP_MODE,
    DEFAULT_GITEE_MCP_URL,
    DEFAULT_GITEE_READ_TOOLS,
    GITEE_API_BASE,
)
from integrations.gitee.config import (
    GiteeMCPConfig,
    build_gitee_mcp_config,
    classify,
    gitee_mcp_config_from_env,
    gitee_mcp_runtime_unavailable_reason,
)
from integrations.gitee.mcp import (
    GiteeMCPContentItem,
    GiteeMCPToolCallResult,
    GiteeMCPToolDescriptor,
    GiteeMCPValidationResult,
    call_gitee_mcp_tool,
    describe_gitee_mcp_error,
    list_gitee_mcp_tools,
    normalize_gitee_tool_result,
    validate_gitee_mcp_config,
)

__all__ = [
    "DEFAULT_GITEE_MCP_MODE",
    "DEFAULT_GITEE_MCP_URL",
    "DEFAULT_GITEE_READ_TOOLS",
    "GITEE_API_BASE",
    "GiteeMCPConfig",
    "GiteeMCPContentItem",
    "GiteeMCPToolCallResult",
    "GiteeMCPToolDescriptor",
    "GiteeMCPValidationResult",
    "build_gitee_mcp_config",
    "call_gitee_mcp_tool",
    "classify",
    "describe_gitee_mcp_error",
    "gitee_mcp_config_from_env",
    "gitee_mcp_runtime_unavailable_reason",
    "list_gitee_mcp_tools",
    "normalize_gitee_tool_result",
    "validate_gitee_mcp_config",
]
