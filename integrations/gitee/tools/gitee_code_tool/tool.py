"""Read-only Gitee code-analysis tools.

These tools wrap the Gitee MCP server's repository read surface:
``list_user_repos``, ``get_file_content``, ``search_files_by_content``, and
``compare_branches_tags``. They are all read-only and record evidence entries
so the final answer can cite the files and searches it used.
"""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.gitee.tools._shared import (
    AUTH_PARAM_NAMES,
    AUTH_PROPERTIES,
    available,
    config_from_args,
    decode,
    dispatch,
    extract_params,
    file_view,
    record,
)


def _map_list_gitee_repos(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    if not output.get("available"):
        return
    record(
        evidence,
        base="list_gitee_repos",
        label="Gitee Repositories",
        summary=f"{output.get('count', 0)} repositories",
    )


def _map_list_gitee_tree(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    if not output.get("available") or output.get("kind") != "directory":
        return
    record(
        evidence,
        base="list_gitee_tree",
        label="Gitee Directory",
        summary=f"{output.get('count', 0)} items: {output.get('path') or '/'}",
    )


def _map_get_gitee_file(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    if not output.get("available"):
        return
    if output.get("kind") == "file" and output.get("content"):
        record(
            evidence,
            base="get_gitee_file",
            label="Gitee File Contents",
            summary=f"File: {output.get('path')}",
        )


def _map_search_gitee_files(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    if not output.get("available"):
        return
    record(
        evidence,
        base="search_gitee_files",
        label="Gitee Code Search",
        summary=f"{output.get('count', 0)} matches: {output.get('query', '')}",
    )


def _map_compare_gitee_refs(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    if not output.get("available"):
        return
    record(
        evidence,
        base="compare_gitee_refs",
        label="Gitee Ref Comparison",
        summary=f"{output.get('base')}...{output.get('head')}",
    )


@tool(
    name="list_gitee_repos",
    source="gitee_mcp",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description=(
        "List Gitee repositories the configured token can read. Use it to resolve "
        "the exact owner/repo before browsing code."
    ),
    use_cases=[
        "Finding the repository named in an alert or a user question",
        "Checking which private or organization repositories the token can read",
    ],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "visibility": {
                "type": "string",
                "enum": ["all", "public", "private"],
                "default": "all",
            },
            "affiliation": {
                "type": "string",
                "description": "owner, collaborator, organization_member, enterprise_member, or admin",
            },
            "q": {"type": "string", "description": "Optional repository search keywords."},
            "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            **AUTH_PROPERTIES,
        },
        "required": [],
    },
    is_available=available,
    extract_params=extract_params,
    injected_params=AUTH_PARAM_NAMES,
    evidence_mapper=_map_list_gitee_repos,
)
def list_gitee_repos(
    visibility: str = "all",
    affiliation: str = "",
    q: str = "",
    per_page: int = 20,
    gitee_url: str | None = None,
    gitee_mode: str | None = None,
    gitee_token: str | None = None,
    gitee_command: str | None = None,
    gitee_args: list[str] | None = None,
    gitee_enabled_tools: list[str] | None = None,
    gitee_timeout_seconds: float | None = None,
    **_kwargs: object,
) -> dict[str, object]:
    """List repositories visible to the configured Gitee token."""
    config, error = config_from_args(
        gitee_url,
        gitee_mode,
        gitee_token,
        gitee_command,
        gitee_args,
        gitee_enabled_tools,
        gitee_timeout_seconds,
    )
    if error is not None:
        return error
    assert config is not None

    arguments: dict[str, object] = {
        "visibility": visibility,
        "per_page": max(1, min(per_page, 100)),
    }
    if affiliation:
        arguments["affiliation"] = affiliation
    if q:
        arguments["q"] = q

    payload, error = dispatch(config, "list_user_repos", arguments)
    if error is not None:
        return error
    assert payload is not None

    decoded = decode(payload)
    repositories = decoded if isinstance(decoded, list) else []
    return {
        "source": "gitee_mcp",
        "available": True,
        "count": len(repositories),
        "repositories": repositories,
    }


@tool(
    name="list_gitee_tree",
    source="gitee_mcp",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description=(
        "List one directory level in a Gitee repository. Pass an empty path for "
        "the repository root, then descend into the directories the listing "
        "returns. Uses the Gitee contents API, which also serves single files."
    ),
    use_cases=[
        "Understanding repository structure before reading code",
        "Finding the directory that matches a stack frame or an alert",
        "Checking whether a config or workflow path exists on a branch",
    ],
    requires=["owner", "repo"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "path": {
                "type": "string",
                "default": "",
                "description": "Directory path; empty means the repository root.",
            },
            "ref": {"type": "string", "description": "Branch, tag, or commit; defaults to the default branch."},
            **AUTH_PROPERTIES,
        },
        "required": ["owner", "repo"],
    },
    is_available=available,
    extract_params=extract_params,
    injected_params=AUTH_PARAM_NAMES,
    evidence_mapper=_map_list_gitee_tree,
)
def list_gitee_tree(
    owner: str,
    repo: str,
    path: str = "",
    ref: str = "",
    gitee_url: str | None = None,
    gitee_mode: str | None = None,
    gitee_token: str | None = None,
    gitee_command: str | None = None,
    gitee_args: list[str] | None = None,
    gitee_enabled_tools: list[str] | None = None,
    gitee_timeout_seconds: float | None = None,
    **_kwargs: object,
) -> dict[str, object]:
    """List one directory level in a Gitee repository."""
    config, error = config_from_args(
        gitee_url,
        gitee_mode,
        gitee_token,
        gitee_command,
        gitee_args,
        gitee_enabled_tools,
        gitee_timeout_seconds,
    )
    if error is not None:
        return error
    assert config is not None

    arguments: dict[str, object] = {"owner": owner, "repo": repo, "path": path}
    if ref:
        arguments["ref"] = ref

    payload, error = dispatch(config, "get_file_content", arguments)
    if error is not None:
        return error
    assert payload is not None

    decoded = decode(payload)
    if isinstance(decoded, list):
        return {
            "source": "gitee_mcp",
            "available": True,
            "kind": "directory",
            "owner": owner,
            "repo": repo,
            "path": path,
            "ref": ref,
            "count": len(decoded),
            "entries": decoded,
            }

    kind = str(decoded.get("type") or "file") if isinstance(decoded, dict) else "file"
    return {
        "source": "gitee_mcp",
        "available": True,
        "kind": kind,
        "owner": owner,
        "repo": repo,
        "path": path,
        "ref": ref,
        "count": 0,
        "entries": [],
        "note": "The path is a file, not a directory. Use get_gitee_file to read it.",
    }


@tool(
    name="get_gitee_file",
    source="gitee_mcp",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description=(
        "Read a file from a Gitee repository at a branch, tag, or commit. Large "
        "files are truncated to max_chars; narrow with search_gitee_files first "
        "when the file is huge."
    ),
    use_cases=[
        "Reading application code referenced by an alert or stack trace",
        "Inspecting CI config, manifests, and deployment files",
        "Checking how a path looked on a branch or commit",
    ],
    requires=["owner", "repo", "path"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "path": {"type": "string"},
            "ref": {"type": "string"},
            "max_chars": {
                "type": "integer",
                "minimum": 1000,
                "maximum": 200000,
                "default": 60000,
                "description": "Truncate the returned file content to this many characters.",
            },
            **AUTH_PROPERTIES,
        },
        "required": ["owner", "repo", "path"],
    },
    is_available=available,
    extract_params=extract_params,
    injected_params=AUTH_PARAM_NAMES,
    evidence_mapper=_map_get_gitee_file,
)
def get_gitee_file(
    owner: str,
    repo: str,
    path: str,
    ref: str = "",
    max_chars: int = 60000,
    gitee_url: str | None = None,
    gitee_mode: str | None = None,
    gitee_token: str | None = None,
    gitee_command: str | None = None,
    gitee_args: list[str] | None = None,
    gitee_enabled_tools: list[str] | None = None,
    gitee_timeout_seconds: float | None = None,
    **_kwargs: object,
) -> dict[str, object]:
    """Read a file from a Gitee repository."""
    config, error = config_from_args(
        gitee_url,
        gitee_mode,
        gitee_token,
        gitee_command,
        gitee_args,
        gitee_enabled_tools,
        gitee_timeout_seconds,
    )
    if error is not None:
        return error
    assert config is not None

    arguments: dict[str, object] = {"owner": owner, "repo": repo, "path": path}
    if ref:
        arguments["ref"] = ref

    payload, error = dispatch(config, "get_file_content", arguments)
    if error is not None:
        return error
    assert payload is not None

    view = file_view(decode(payload), max_chars=max(1000, min(max_chars, 200000)))
    return {
        "source": "gitee_mcp",
        "available": True,
        "owner": owner,
        "repo": repo,
        "path": path,
        "ref": ref,
        **view,
    }


@tool(
    name="search_gitee_files",
    source="gitee_mcp",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description=(
        "Search file contents in a Gitee repository. Use it to find the code "
        "behind an exception, a symbol, or a config key before reading files."
    ),
    use_cases=[
        "Finding source code related to failures, exceptions, and stack frames",
        "Tracing config, workflow, or application code that may explain an incident",
        "Locating call sites for a symbol before reading a specific file",
    ],
    requires=["owner", "repo", "query"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "query": {"type": "string"},
            "ref": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "paths": {
                "type": "string",
                "description": "Comma-separated paths or glob patterns to narrow the search.",
            },
            "before_context": {"type": "integer", "minimum": 0, "default": 0},
            "after_context": {"type": "integer", "minimum": 0, "default": 0},
            **AUTH_PROPERTIES,
        },
        "required": ["owner", "repo", "query"],
    },
    is_available=available,
    extract_params=extract_params,
    injected_params=AUTH_PARAM_NAMES,
    evidence_mapper=_map_search_gitee_files,
)
def search_gitee_files(
    owner: str,
    repo: str,
    query: str,
    ref: str = "",
    limit: int = 20,
    paths: str = "",
    before_context: int = 0,
    after_context: int = 0,
    gitee_url: str | None = None,
    gitee_mode: str | None = None,
    gitee_token: str | None = None,
    gitee_command: str | None = None,
    gitee_args: list[str] | None = None,
    gitee_enabled_tools: list[str] | None = None,
    gitee_timeout_seconds: float | None = None,
    **_kwargs: object,
) -> dict[str, object]:
    """Search file contents in a Gitee repository."""
    config, error = config_from_args(
        gitee_url,
        gitee_mode,
        gitee_token,
        gitee_command,
        gitee_args,
        gitee_enabled_tools,
        gitee_timeout_seconds,
    )
    if error is not None:
        return error
    assert config is not None

    arguments: dict[str, object] = {
        "owner": owner,
        "repo": repo,
        "query": query,
        "limit": max(1, min(limit, 100)),
    }
    if ref:
        arguments["ref"] = ref
    if paths:
        arguments["paths"] = paths
    if before_context:
        arguments["before_context"] = before_context
    if after_context:
        arguments["after_context"] = after_context

    payload, error = dispatch(config, "search_files_by_content", arguments)
    if error is not None:
        return error
    assert payload is not None

    decoded = decode(payload)
    matches = decoded if isinstance(decoded, list) else []
    return {
        "source": "gitee_mcp",
        "available": True,
        "owner": owner,
        "repo": repo,
        "query": query,
        "ref": ref,
        "count": len(matches),
        "matches": matches,
    }


@tool(
    name="compare_gitee_refs",
    source="gitee_mcp",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description=(
        "Compare two branches, tags, or commits in a Gitee repository. Use it to "
        "see which files changed between a known-good ref and the failing one."
    ),
    use_cases=[
        "Linking a deploy or release to the code that changed",
        "Checking what changed between two tags before an incident",
        "Narrowing a regression to a commit range",
    ],
    requires=["owner", "repo", "base", "head"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "base": {"type": "string", "description": "Base branch, tag, or commit SHA."},
            "head": {"type": "string", "description": "Head branch, tag, or commit SHA."},
            **AUTH_PROPERTIES,
        },
        "required": ["owner", "repo", "base", "head"],
    },
    is_available=available,
    extract_params=extract_params,
    injected_params=AUTH_PARAM_NAMES,
    evidence_mapper=_map_compare_gitee_refs,
)
def compare_gitee_refs(
    owner: str,
    repo: str,
    base: str,
    head: str,
    gitee_url: str | None = None,
    gitee_mode: str | None = None,
    gitee_token: str | None = None,
    gitee_command: str | None = None,
    gitee_args: list[str] | None = None,
    gitee_enabled_tools: list[str] | None = None,
    gitee_timeout_seconds: float | None = None,
    **_kwargs: object,
) -> dict[str, object]:
    """Compare two Gitee refs."""
    config, error = config_from_args(
        gitee_url,
        gitee_mode,
        gitee_token,
        gitee_command,
        gitee_args,
        gitee_enabled_tools,
        gitee_timeout_seconds,
    )
    if error is not None:
        return error
    assert config is not None

    payload, error = dispatch(
        config,
        "compare_branches_tags",
        {"owner": owner, "repo": repo, "base": base, "head": head},
    )
    if error is not None:
        return error
    assert payload is not None

    return {
        "source": "gitee_mcp",
        "available": True,
        "owner": owner,
        "repo": repo,
        "base": base,
        "head": head,
        "compare": decode(payload),
    }
