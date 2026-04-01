"""GitHub MCP-backed repository investigation actions."""

from __future__ import annotations

from typing import Any

from app.integrations.github_mcp import (
    GitHubMCPConfig,
    build_github_code_search_query,
    build_github_mcp_config,
    call_github_mcp_tool,
    github_mcp_config_from_env,
)


def _resolve_config(
    github_url: str | None,
    github_mode: str | None,
    github_token: str | None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
) -> GitHubMCPConfig | None:
    env_config = github_mcp_config_from_env()
    if any([github_url, github_mode, github_token, github_command, github_args]):
        return build_github_mcp_config({
            "url": github_url or (env_config.url if env_config else ""),
            "mode": github_mode or (env_config.mode if env_config else ""),
            "auth_token": github_token or (env_config.auth_token if env_config else ""),
            "command": github_command or (env_config.command if env_config else ""),
            "args": github_args or (list(env_config.args) if env_config else []),
            "headers": env_config.headers if env_config else {},
            "toolsets": env_config.toolsets if env_config else (),
        })
    return env_config


def _normalize_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("is_error"):
        return {
            "source": "github",
            "available": False,
            "error": result.get("text") or "GitHub MCP tool call failed.",
            "tool": result.get("tool"),
            "arguments": result.get("arguments", {}),
        }
    return {
        "source": "github",
        "available": True,
        "tool": result.get("tool"),
        "arguments": result.get("arguments", {}),
        "text": result.get("text", ""),
        "structured_content": result.get("structured_content"),
        "content": result.get("content", []),
    }


def search_github_code(
    owner: str,
    repo: str,
    query: str,
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Search GitHub repository code through the configured GitHub MCP server.

    Useful for:
    - Investigating alerts that mention a repository, branch, or commit
    - Finding source code related to failures, exceptions, and stack frames
    - Tracing config, workflow, or application code that may explain an incident

    Args:
        owner: GitHub repository owner or organization
        repo: GitHub repository name
        query: Repository-scoped GitHub code search query
        github_url: GitHub MCP URL
        github_mode: GitHub MCP transport mode
        github_token: GitHub PAT for MCP auth

    Returns:
        matches: Matching code search results from GitHub
        query: Final repo-scoped search query
    """
    config = _resolve_config(github_url, github_mode, github_token, github_command, github_args)
    if config is None:
        return {
            "source": "github",
            "available": False,
            "error": "GitHub MCP integration is not configured.",
            "matches": [],
        }

    final_query = build_github_code_search_query(owner, repo, query)
    result = call_github_mcp_tool(config, "search_code", {"query": final_query})
    payload = _normalize_tool_result(result)
    payload["matches"] = payload.pop("structured_content", None)
    payload["query"] = final_query
    return payload


def get_github_file_contents(
    owner: str,
    repo: str,
    path: str,
    ref: str = "",
    sha: str = "",
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch a file or directory from GitHub through the MCP server.

    Useful for:
    - Reading application code referenced by an alert
    - Inspecting CI config, manifests, and deployment files
    - Checking how a specific path looked on a branch or commit

    Args:
        owner: GitHub repository owner or organization
        repo: GitHub repository name
        path: File or directory path inside the repository
        ref: Optional git ref
        sha: Optional commit SHA
        github_url: GitHub MCP URL
        github_mode: GitHub MCP transport mode
        github_token: GitHub PAT for MCP auth

    Returns:
        file: File or directory contents from GitHub
    """
    config = _resolve_config(github_url, github_mode, github_token, github_command, github_args)
    if config is None:
        return {
            "source": "github",
            "available": False,
            "error": "GitHub MCP integration is not configured.",
            "file": {},
        }

    arguments = {"owner": owner, "repo": repo, "path": path}
    if ref:
        arguments["ref"] = ref
    if sha:
        arguments["sha"] = sha
    result = call_github_mcp_tool(config, "get_file_contents", arguments)
    payload = _normalize_tool_result(result)
    payload["file"] = payload.pop("structured_content", None)
    return payload


def get_github_repository_tree(
    owner: str,
    repo: str,
    path_filter: str = "",
    recursive: bool = True,
    tree_sha: str = "",
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Browse a GitHub repository tree through the MCP server.

    Useful for:
    - Understanding repository structure during an incident
    - Finding likely directories for runtime code, configs, or workflows
    - Narrowing down where to read code next

    Args:
        owner: GitHub repository owner or organization
        repo: GitHub repository name
        path_filter: Optional path prefix to limit the tree
        recursive: Whether to fetch the tree recursively
        tree_sha: Optional branch, tag, or SHA to inspect
        github_url: GitHub MCP URL
        github_mode: GitHub MCP transport mode
        github_token: GitHub PAT for MCP auth

    Returns:
        tree: Repository tree payload from GitHub
    """
    config = _resolve_config(github_url, github_mode, github_token, github_command, github_args)
    if config is None:
        return {
            "source": "github",
            "available": False,
            "error": "GitHub MCP integration is not configured.",
            "tree": {},
        }

    arguments: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "recursive": recursive,
    }
    if path_filter:
        arguments["path_filter"] = path_filter
    if tree_sha:
        arguments["tree_sha"] = tree_sha

    result = call_github_mcp_tool(config, "get_repository_tree", arguments)
    payload = _normalize_tool_result(result)
    payload["tree"] = payload.pop("structured_content", None)
    return payload


def list_github_commits(
    owner: str,
    repo: str,
    path: str = "",
    sha: str = "",
    per_page: int = 10,
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List recent commits for a GitHub repository through the MCP server.

    Useful for:
    - Checking whether a recent change could explain a failure
    - Reviewing commit history for a specific file or directory
    - Correlating a deployment or incident window with code changes

    Args:
        owner: GitHub repository owner or organization
        repo: GitHub repository name
        path: Optional file path to scope commit history
        sha: Optional branch, tag, or commit SHA
        per_page: Maximum number of commits to fetch
        github_url: GitHub MCP URL
        github_mode: GitHub MCP transport mode
        github_token: GitHub PAT for MCP auth

    Returns:
        commits: Commit history payload from GitHub
    """
    config = _resolve_config(github_url, github_mode, github_token, github_command, github_args)
    if config is None:
        return {
            "source": "github",
            "available": False,
            "error": "GitHub MCP integration is not configured.",
            "commits": [],
        }

    arguments: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "perPage": per_page,
    }
    if path:
        arguments["path"] = path
    if sha:
        arguments["sha"] = sha

    result = call_github_mcp_tool(config, "list_commits", arguments)
    payload = _normalize_tool_result(result)
    payload["commits"] = payload.pop("structured_content", None)
    return payload
