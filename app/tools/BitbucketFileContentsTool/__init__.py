"""Bitbucket File Contents Tool."""

from __future__ import annotations

from typing import Any

from app.integrations.bitbucket import get_file_contents
from app.tools.BitbucketSearchCodeTool import (
    _bb_available,
    _bb_creds,
    _resolve_config,
)
from app.tools.tool_decorator import tool


def _get_bitbucket_file_contents_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    bb = sources["bitbucket"]
    return {
        "repo_slug": bb.get("repo_slug", bb.get("repo", "")),
        "path": bb["path"],
        "ref": bb.get("ref", ""),
        **_bb_creds(bb),
    }


def _get_bitbucket_file_contents_available(sources: dict[str, dict]) -> bool:
    bb = sources.get("bitbucket", {})
    return bool(_bb_available(sources) and bb.get("repo_slug", bb.get("repo")) and bb.get("path"))


@tool(
    name="get_bitbucket_file_contents",
    description="Retrieve the contents of a file from a Bitbucket repository at a specific revision.",
    source="bitbucket",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Reading configuration files that may explain a failure",
        "Comparing file contents between revisions during investigation",
    ],
    requires=["repo_slug", "path"],
    input_schema={
        "type": "object",
        "properties": {
            "repo_slug": {"type": "string"},
            "path": {"type": "string"},
            "ref": {"type": "string", "default": ""},
            "workspace": {"type": "string"},
            "username": {"type": "string"},
            "app_password": {"type": "string"},
            "base_url": {"type": "string"},
            "max_results": {"type": "integer"},
            "integration_id": {"type": "string"},
        },
        "required": ["repo_slug", "path"],
    },
    is_available=_get_bitbucket_file_contents_available,
    extract_params=_get_bitbucket_file_contents_extract_params,
)
def get_bitbucket_file_contents(
    repo_slug: str,
    path: str,
    workspace: str | None = None,
    username: str | None = None,
    app_password: str | None = None,
    base_url: str | None = None,
    max_results: int | None = None,
    integration_id: str | None = None,
    ref: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch file contents from a Bitbucket repository."""
    config = _resolve_config(
        workspace,
        username,
        app_password,
        base_url,
        max_results,
        integration_id,
    )
    if config is None:
        return {
            "source": "bitbucket",
            "available": False,
            "error": "Bitbucket integration is not configured.",
            "file": {},
        }
    return get_file_contents(config, repo_slug=repo_slug, path=path, ref=ref)
