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


def _unwrap_exception_message(exc: BaseException) -> str:
    """Unwrap ExceptionGroup / BaseExceptionGroup to a human-readable message."""
    # ExceptionGroup (Python 3.11+) wraps multiple exceptions; unwrap to the first one.
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return _unwrap_exception_message(exc.exceptions[0])
    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, BaseException):
        return _unwrap_exception_message(cause)
    context = getattr(exc, "__context__", None)
    if isinstance(context, BaseException) and not getattr(exc, "__suppress_context__", False):
        return _unwrap_exception_message(context)
    return f"{type(exc).__name__}: {exc}"


def _list_github_commits_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    return {
        "owner": gh.get("owner"),
        "repo": gh.get("repo"),
        "path": gh.get("path", ""),
        "sha": gh.get("sha") or gh.get("ref", ""),
        "per_page": 10,
        **github_creds(gh),
    }


def _list_github_commits_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(github_source_available(sources) and gh.get("owner") and gh.get("repo"))


def _map_list_github_commits(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    commits = output.get("commits", [])
    if commits:
        count = len(commits)
        word = "commit" if count == 1 else "commits"
        record_evidence_entry(
            evidence,
            source="list_github_commits",
            label="GitHub Commits",
            summary=f"{count} {word}",
        )


@tool(
    name="list_github_commits",
    source="github",
    description="List recent commits for a GitHub repository through the MCP server.",
    use_cases=[
        "Checking whether a recent change could explain a failure",
        "Reviewing commit history for a specific file or directory",
        "Correlating a deployment or incident window with code changes",
    ],
    requires=["owner", "repo"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "path": {"type": "string", "default": ""},
            "sha": {"type": "string", "default": ""},
            "per_page": {"type": "integer", "default": 10},
            "github_url": {"type": "string"},
            "github_mode": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_list_github_commits_available,
    is_advertised=github_source_available,
    extract_params=_list_github_commits_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
    evidence_mapper=_map_list_github_commits,
)
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
    """List recent commits for a GitHub repository through the MCP server."""
    try:
        config = resolve_github_mcp_config(
            github_url, github_mode, github_token, github_command, github_args
        )
        if config is None:
            return tool_unavailable(
                "github", "GitHub MCP integration is not configured.", commits=[]
            )

        arguments: dict[str, Any] = {"owner": owner, "repo": repo, "perPage": per_page}
        if path:
            arguments["path"] = path
        if sha:
            arguments["sha"] = sha

        result = call_github_mcp_tool(config, "list_commits", arguments)
        payload = normalize_github_tool_result(result)
        payload["commits"] = payload.pop("structured_content", None)
        return payload
    except Exception as exc:
        # MCP session cleanup can raise ExceptionGroup (anyio TaskGroup) when both
        # the tool call and the background receive-loop fail simultaneously.
        # Catch here to ensure the tool always returns an error dict rather than raising.
        error_msg = _unwrap_exception_message(exc)
        return tool_unavailable("github", error_msg, commits=[])
