"""GitHub MCP-backed repository investigation tools."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import code_host_unavailable_payload
from integrations.github.envelope import normalize_github_tool_result
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_creds,
    github_source_available,
    resolve_github_mcp_config,
)
from integrations.github.mcp import (
    build_github_code_search_query,
    call_github_mcp_tool,
)


def _search_github_code_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    return {
        "owner": gh.get("owner"),
        "repo": gh.get("repo"),
        "query": gh.get("query") or "exception OR error",
        **github_creds(gh),
    }


def _search_github_code_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(github_source_available(sources) and gh.get("owner") and gh.get("repo"))


def _map_search_github_code(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    matches = output.get("matches")
    if not isinstance(matches, list) or not matches:
        return
    query = output.get("query", "")
    record_evidence_entry(
        evidence,
        source="search_github_code",
        label="GitHub Code Search",
        summary=f"{len(matches)} matches: {query}" if query else f"{len(matches)} matches",
    )


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
    surfaces=(ToolSurface.CHAT,),
    evidence_mapper=_map_search_github_code,
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
    is_advertised=github_source_available,
    extract_params=_search_github_code_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
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
    config = resolve_github_mcp_config(
        github_url, github_mode, github_token, github_command, github_args
    )
    if config is None:
        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub MCP",
            empty_key="matches",
            empty_value=[],
        )

    final_query = build_github_code_search_query(owner, repo, query)
    result = call_github_mcp_tool(config, "search_code", {"query": final_query})
    payload = normalize_github_tool_result(result)
    payload["matches"] = payload.pop("structured_content", None)
    payload["query"] = final_query
    return payload
