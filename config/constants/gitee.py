"""Gitee MCP environment variable names and shared defaults."""

from __future__ import annotations

from typing import Final

DEFAULT_GITEE_MCP_MODE: Final = "streamable-http"
DEFAULT_GITEE_MCP_URL: Final = "https://api.gitee.com/mcp"
GITEE_API_BASE: Final = "https://gitee.com/api/v5"

#: Read-only tools the code-analysis path needs. Keeping this allowlist on by
#: default means a Gitee token with repository read scope can never create,
#: merge, comment, or push, even through the generic bridge.
DEFAULT_GITEE_READ_TOOLS: Final[tuple[str, ...]] = (
    "list_user_repos",
    "get_file_content",
    "search_files_by_content",
    "compare_branches_tags",
    "get_diff_files",
    "list_repo_pulls",
    "list_repo_issues",
    "get_repo_issue_detail",
    "list_comments",
)
GITEE_EXPECTED_CODE_TOOLS: Final[frozenset[str]] = frozenset(
    {"get_file_content", "search_files_by_content"}
)

GITEE_ACCESS_TOKEN_ENV = "GITEE_ACCESS_TOKEN"
GITEE_MCP_URL_ENV = "GITEE_MCP_URL"
GITEE_MCP_AUTH_TOKEN_ENV = "GITEE_MCP_AUTH_TOKEN"
GITEE_MCP_MODE_ENV = "GITEE_MCP_MODE"
GITEE_MCP_COMMAND_ENV = "GITEE_MCP_COMMAND"
GITEE_MCP_ARGS_ENV = "GITEE_MCP_ARGS"
GITEE_MCP_TIMEOUT_SECONDS_ENV = "GITEE_MCP_TIMEOUT_SECONDS"
GITEE_MCP_ENABLED_TOOLS_ENV = "GITEE_MCP_ENABLED_TOOLS"

__all__ = [
    "DEFAULT_GITEE_MCP_MODE",
    "DEFAULT_GITEE_MCP_URL",
    "DEFAULT_GITEE_READ_TOOLS",
    "GITEE_ACCESS_TOKEN_ENV",
    "GITEE_API_BASE",
    "GITEE_EXPECTED_CODE_TOOLS",
    "GITEE_MCP_ARGS_ENV",
    "GITEE_MCP_AUTH_TOKEN_ENV",
    "GITEE_MCP_COMMAND_ENV",
    "GITEE_MCP_ENABLED_TOOLS_ENV",
    "GITEE_MCP_MODE_ENV",
    "GITEE_MCP_TIMEOUT_SECONDS_ENV",
    "GITEE_MCP_URL_ENV",
]
