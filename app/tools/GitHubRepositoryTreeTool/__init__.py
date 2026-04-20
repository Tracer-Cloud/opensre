"""GitHub MCP-backed repository investigation tools."""

from __future__ import annotations

from typing import Any

from app.integrations.github_mcp import call_github_mcp_tool
from app.tools.GitHubSearchCodeTool import (
    _gh_available,
    _gh_creds,
    _normalize_tool_result,
    _resolve_config,
)
from app.tools.tool_decorator import tool


def _get_github_repository_tree_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources["github"]
    return {
        "owner": gh["owner"],
        "repo": gh["repo"],
        "path_filter": gh.get("path", ""),
        "tree_sha": gh.get("sha") or gh.get("ref", ""),
        "recursive": True,
        **_gh_creds(gh),
    }


def _get_github_repository_tree_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(_gh_available(sources) and gh.get("owner") and gh.get("repo"))


@tool(
    name="get_github_repository_tree",
    source="github",
    description="Browse a GitHub repository tree through the MCP server.",
    use_cases=[
        "Understanding repository structure during an incident",
        "Finding likely directories for runtime code, configs, or workflows",
        "Narrowing down where to read code next",
    ],
    requires=["owner", "repo"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "path_filter": {"type": "string", "default": ""},
            "recursive": {"type": "boolean", "default": True},
            "tree_sha": {"type": "string", "default": ""},
            "github_url": {"type": "string"},
            "github_mode": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_get_github_repository_tree_available,
    extract_params=_get_github_repository_tree_extract_params,
)
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
    """Browse a GitHub repository tree through the MCP server."""
    config = _resolve_config(github_url, github_mode, github_token, github_command, github_args)
    if config is None:
        return {
            "source": "github",
            "available": False,
            "error": "GitHub MCP integration is not configured.",
            "tree": {},
        }

    arguments: dict[str, Any] = {"owner": owner, "repo": repo, "recursive": recursive}
    if path_filter:
        arguments["path_filter"] = path_filter
    if tree_sha:
        arguments["tree_sha"] = tree_sha

    result = call_github_mcp_tool(config, "get_repository_tree", arguments)
    payload = _normalize_tool_result(result)
    payload["tree"] = payload.pop("structured_content", None)
    return payload
