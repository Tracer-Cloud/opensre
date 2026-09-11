"""GitHub MCP-backed repository investigation tools."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.github.envelope import normalize_github_tool_result
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_creds,
    github_source_available,
    resolve_github_mcp_config,
)
from integrations.github.mcp import call_github_mcp_tool


def _get_github_repository_tree_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    return {
        "owner": gh.get("owner"),
        "repo": gh.get("repo"),
        "path_filter": gh.get("path", ""),
        "tree_sha": gh.get("sha") or gh.get("ref", ""),
        "recursive": True,
        **github_creds(gh),
    }


def _get_github_repository_tree_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(github_source_available(sources) and gh.get("owner") and gh.get("repo"))


def _map_get_github_repository_tree(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    tree = output.get("tree")
    if isinstance(tree, dict) and isinstance(tree.get("tree"), list):
        count = len(tree["tree"])
        word = "item" if count == 1 else "items"
        record_evidence_entry(
            evidence,
            source="get_github_repository_tree",
            label="GitHub Repository Tree",
            summary=f"{count} {word}",
        )


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
    surfaces=(ToolSurface.CHAT,),
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
    is_advertised=github_source_available,
    extract_params=_get_github_repository_tree_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
    evidence_mapper=_map_get_github_repository_tree,
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
    config = resolve_github_mcp_config(
        github_url, github_mode, github_token, github_command, github_args
    )
    if config is None:
        return tool_unavailable("github", "GitHub MCP integration is not configured.", tree={})

    arguments: dict[str, Any] = {"owner": owner, "repo": repo, "recursive": recursive}
    if path_filter:
        arguments["path_filter"] = path_filter
    if tree_sha:
        arguments["tree_sha"] = tree_sha

    result = call_github_mcp_tool(config, "get_repository_tree", arguments)
    payload = normalize_github_tool_result(result)
    payload["tree"] = payload.pop("structured_content", None)
    return payload
