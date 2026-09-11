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
from integrations.github.mcp import call_github_mcp_tool


def _file_from_resource_text(content: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return ``{uri, content}`` from the first ``resource_text`` content item.

    Returns ``None`` when no such item is present (e.g. a binary file arrives
    as ``resource_blob`` instead).
    """
    for item in content:
        if item.get("type") == "resource_text":
            return {"uri": item.get("uri", ""), "content": item.get("text", "")}
    return None


def _get_github_file_contents_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    return {
        "owner": gh.get("owner"),
        "repo": gh.get("repo"),
        "path": gh.get("path", ""),
        "ref": gh.get("ref", ""),
        "sha": gh.get("sha", ""),
        **github_creds(gh),
    }


def _get_github_file_contents_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(
        github_source_available(sources) and gh.get("owner") and gh.get("repo") and gh.get("path")
    )


def _map_get_github_file_contents(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    file_data = output.get("file")
    if file_data and isinstance(file_data, dict) and file_data.get("content"):
        path = output.get("path") or _input.get("path", "unknown")
        record_evidence_entry(
            evidence,
            source="get_github_file_contents",
            label="GitHub File Contents",
            summary=f"File: {path}",
        )


@tool(
    name="get_github_file_contents",
    source="github",
    description="Fetch a file or directory from GitHub through the MCP server.",
    use_cases=[
        "Reading application code referenced by an alert",
        "Inspecting CI config, manifests, and deployment files",
        "Checking how a specific path looked on a branch or commit",
    ],
    requires=["owner", "repo", "path"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "path": {"type": "string"},
            "ref": {"type": "string", "default": ""},
            "sha": {"type": "string", "default": ""},
            "github_url": {"type": "string"},
            "github_mode": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo", "path"],
    },
    is_available=_get_github_file_contents_available,
    is_advertised=github_source_available,
    extract_params=_get_github_file_contents_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
    evidence_mapper=_map_get_github_file_contents,
)
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
    """Fetch a file or directory from GitHub through the MCP server."""
    config = resolve_github_mcp_config(
        github_url, github_mode, github_token, github_command, github_args
    )
    if config is None:
        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub MCP",
            empty_key="file",
            empty_value={},
        )

    arguments = {"owner": owner, "repo": repo, "path": path}
    if ref:
        arguments["ref"] = ref
    if sha:
        arguments["sha"] = sha
    result = call_github_mcp_tool(config, "get_file_contents", arguments)
    payload = normalize_github_tool_result(result)
    structured = payload.pop("structured_content", None)
    if structured is None:
        structured = _file_from_resource_text(payload.get("content", []))
    payload["file"] = structured
    return payload
