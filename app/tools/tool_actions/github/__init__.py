"""GitHub investigation tools."""

from app.tools.tool_actions.base import BaseTool
from app.tools.tool_actions.github.github_mcp_actions import (
    GitHubCommitsTool,
    GitHubFileContentsTool,
    GitHubRepositoryTreeTool,
    GitHubSearchCodeTool,
    get_github_file_contents,
    get_github_repository_tree,
    list_github_commits,
    search_github_code,
)

TOOLS: list[BaseTool] = [
    GitHubSearchCodeTool(),
    GitHubFileContentsTool(),
    GitHubRepositoryTreeTool(),
    GitHubCommitsTool(),
]

__all__ = [
    "TOOLS",
    "GitHubCommitsTool",
    "GitHubFileContentsTool",
    "GitHubRepositoryTreeTool",
    "GitHubSearchCodeTool",
    "get_github_file_contents",
    "get_github_repository_tree",
    "list_github_commits",
    "search_github_code",
]
