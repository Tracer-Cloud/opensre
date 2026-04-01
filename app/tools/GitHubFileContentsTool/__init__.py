"""GitHub MCP-backed repository investigation tools."""

from __future__ import annotations

from typing import Any

from app.integrations.github_mcp import call_github_mcp_tool
from app.tools.base import BaseTool
from app.tools.GitHubSearchCodeTool import (
    _gh_available,
    _gh_creds,
    _normalize_tool_result,
    _resolve_config,
)


class GitHubFileContentsTool(BaseTool):
    """Fetch a file or directory from GitHub through the MCP server."""

    name = "get_github_file_contents"
    source = "github"
    description = "Fetch a file or directory from GitHub through the MCP server."
    use_cases = [
        "Reading application code referenced by an alert",
        "Inspecting CI config, manifests, and deployment files",
        "Checking how a specific path looked on a branch or commit",
    ]
    requires = ["owner", "repo", "path"]
    input_schema = {
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
    }

    def is_available(self, sources: dict) -> bool:
        gh = sources.get("github", {})
        return bool(_gh_available(sources) and gh.get("owner") and gh.get("repo") and gh.get("path"))

    def extract_params(self, sources: dict) -> dict:
        gh = sources["github"]
        return {
            "owner": gh["owner"],
            "repo": gh["repo"],
            "path": gh["path"],
            "ref": gh.get("ref", ""),
            "sha": gh.get("sha", ""),
            **_gh_creds(gh),
        }

    def run(
        self,
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
        config = _resolve_config(github_url, github_mode, github_token, github_command, github_args)
        if config is None:
            return {"source": "github", "available": False, "error": "GitHub MCP integration is not configured.", "file": {}}

        arguments = {"owner": owner, "repo": repo, "path": path}
        if ref:
            arguments["ref"] = ref
        if sha:
            arguments["sha"] = sha
        result = call_github_mcp_tool(config, "get_file_contents", arguments)
        payload = _normalize_tool_result(result)
        payload["file"] = payload.pop("structured_content", None)
        return payload


get_github_file_contents = GitHubFileContentsTool()
