"""GitHub MCP-backed repository investigation tools."""

from __future__ import annotations

from typing import Any

from app.integrations.github_mcp import call_github_mcp_tool
from app.tools.tool_actions.base import BaseTool
from app.tools.tool_actions.GitHubSearchCodeTool import (
    _gh_available,
    _gh_creds,
    _normalize_tool_result,
    _resolve_config,
)


class GitHubCommitsTool(BaseTool):
    """List recent commits for a GitHub repository through the MCP server."""

    name = "list_github_commits"
    source = "github"
    description = "List recent commits for a GitHub repository through the MCP server."
    use_cases = [
        "Checking whether a recent change could explain a failure",
        "Reviewing commit history for a specific file or directory",
        "Correlating a deployment or incident window with code changes",
    ]
    requires = ["owner", "repo"]
    input_schema = {
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
    }

    def is_available(self, sources: dict) -> bool:
        gh = sources.get("github", {})
        return bool(_gh_available(sources) and gh.get("owner") and gh.get("repo"))

    def extract_params(self, sources: dict) -> dict:
        gh = sources["github"]
        return {
            "owner": gh["owner"],
            "repo": gh["repo"],
            "path": gh.get("path", ""),
            "sha": gh.get("sha") or gh.get("ref", ""),
            "per_page": 10,
            **_gh_creds(gh),
        }

    def run(
        self,
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
        config = _resolve_config(github_url, github_mode, github_token, github_command, github_args)
        if config is None:
            return {"source": "github", "available": False, "error": "GitHub MCP integration is not configured.", "commits": []}

        arguments: dict[str, Any] = {"owner": owner, "repo": repo, "perPage": per_page}
        if path:
            arguments["path"] = path
        if sha:
            arguments["sha"] = sha

        result = call_github_mcp_tool(config, "list_commits", arguments)
        payload = _normalize_tool_result(result)
        payload["commits"] = payload.pop("structured_content", None)
        return payload


list_github_commits = GitHubCommitsTool()
