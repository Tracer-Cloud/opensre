"""Bitbucket Commits Tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.bitbucket.client import list_commits
from integrations.bitbucket.tools.availability import bitbucket_available_or_backend
from integrations.bitbucket.tools.bitbucket_commits_tool._evidence import (
    map_list_bitbucket_commits,
)
from integrations.bitbucket.tools.bitbucket_search_code_tool import (
    _bb_creds,
    _resolve_config,
)


def _list_bitbucket_commits_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    bb = sources["bitbucket"]
    return {
        "repo_slug": bb.get("repo_slug", bb.get("repo", "")),
        "path": bb.get("path", ""),
        "limit": 20,
        **_bb_creds(bb),
    }


def _list_bitbucket_commits_available(sources: dict[str, dict]) -> bool:
    bb = sources.get("bitbucket", {})
    return bool(bitbucket_available_or_backend(sources) and bb.get("repo_slug", bb.get("repo")))


@tool(
    name="list_bitbucket_commits",
    description="List recent commits for a Bitbucket repository, optionally filtered by file path.",
    source="bitbucket",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Checking whether a recent change could explain a failure",
        "Reviewing commit history for a specific file or directory",
    ],
    requires=["repo_slug"],
    input_schema={
        "type": "object",
        "properties": {
            "repo_slug": {"type": "string"},
            "path": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 20},
            "workspace": {"type": "string"},
            "username": {"type": "string"},
            "app_password": {"type": "string"},
            "base_url": {"type": "string"},
            "max_results": {"type": "integer"},
            "integration_id": {"type": "string"},
        },
        "required": ["repo_slug"],
    },
    is_available=_list_bitbucket_commits_available,
    extract_params=_list_bitbucket_commits_extract_params,
    evidence_mapper=map_list_bitbucket_commits,
)
def list_bitbucket_commits(
    repo_slug: str,
    workspace: str | None = None,
    username: str | None = None,
    app_password: str | None = None,
    base_url: str | None = None,
    max_results: int | None = None,
    integration_id: str | None = None,
    path: str = "",
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch recent commits from a Bitbucket repository."""
    config = _resolve_config(
        workspace,
        username,
        app_password,
        base_url,
        max_results,
        integration_id,
    )
    if config is None:
        return tool_unavailable("bitbucket", "Bitbucket integration is not configured.", commits=[])
    return list_commits(config, repo_slug=repo_slug, path=path, limit=limit)
