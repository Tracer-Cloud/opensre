"""Bitbucket Commits Tool."""

from __future__ import annotations

from typing import Any

from app.integrations.bitbucket import list_commits
from app.tools.BitbucketSearchCodeTool import (
    _bb_available,
    _bb_creds,
    _resolve_config,
)
from app.tools.tool_decorator import tool


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
    return bool(_bb_available(sources) and bb.get("repo_slug", bb.get("repo")))


@tool(
    name="list_bitbucket_commits",
    description="List recent commits for a Bitbucket repository, optionally filtered by file path.",
    source="bitbucket",
    surfaces=("investigation", "chat"),
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
        },
        "required": ["repo_slug"],
    },
    is_available=_list_bitbucket_commits_available,
    extract_params=_list_bitbucket_commits_extract_params,
)
def list_bitbucket_commits(
    repo_slug: str,
    workspace: str | None = None,
    username: str | None = None,
    app_password: str | None = None,
    path: str = "",
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch recent commits from a Bitbucket repository."""
    config = _resolve_config(workspace, username, app_password)
    if config is None:
        return {
            "source": "bitbucket",
            "available": False,
            "error": "Bitbucket integration is not configured.",
            "commits": [],
        }
    return list_commits(config, repo_slug=repo_slug, path=path, limit=limit)
