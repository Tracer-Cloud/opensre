"""GitHub MCP-backed repository investigation tools."""

from __future__ import annotations

from typing import Any

from app.integrations.github_mcp import (
    GitHubMCPConfig,
    build_github_code_search_query,
    build_github_mcp_config,
    call_github_mcp_tool,
    github_mcp_config_from_env,
)
from app.tools.tool_decorator import tool


def _resolve_config(
    github_url: str | None,
    github_mode: str | None,
    github_token: str | None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
) -> GitHubMCPConfig | None:
    env_config = github_mcp_config_from_env()
    if any([github_url, github_mode, github_token, github_command, github_args]):
        return build_github_mcp_config(
            {
                "url": github_url or (env_config.url if env_config else ""),
                "mode": github_mode or (env_config.mode if env_config else ""),
                "auth_token": github_token or (env_config.auth_token if env_config else ""),
                "command": github_command or (env_config.command if env_config else ""),
                "args": github_args or (list(env_config.args) if env_config else []),
                "headers": env_config.headers if env_config else {},
                "toolsets": env_config.toolsets if env_config else (),
            }
        )
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


def _gh_creds(gh: dict) -> dict:
    return {
        "github_url": gh.get("github_url"),
        "github_mode": gh.get("github_mode", "streamable-http"),
        "github_token": gh.get("github_token"),
        "github_command": gh.get("github_command", ""),
        "github_args": gh.get("github_args", []),
    }


def _gh_available(sources: dict) -> bool:
    return bool(sources.get("github", {}).get("connection_verified"))


def _search_github_code_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources["github"]
    return {
        "owner": gh["owner"],
        "repo": gh["repo"],
        "query": gh.get("query") or "exception OR error",
        **_gh_creds(gh),
    }


def _search_github_code_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(_gh_available(sources) and gh.get("owner") and gh.get("repo"))


@tool(
    name="search_github_code",
    source="github",
    description="Search GitHub repository code through the configured GitHub MCP server.",
    use_cases=[
        "Investigating alerts that mention a repository, branch, or commit",
        "Finding source code related to failures, exceptions, and stack frames",
        "Tracing config, workflow, or application code that may explain an incident",
    ],
    requires=["owner", "repo", "query"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "query": {"type": "string"},
            "github_url": {"type": "string"},
            "github_mode": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo", "query"],
    },
    is_available=_search_github_code_available,
    extract_params=_search_github_code_extract_params,
)
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
    """Search GitHub repository code through the configured GitHub MCP server."""
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
