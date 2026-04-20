"""gitlab repository investigation tools."""

from __future__ import annotations

from typing import Any

from app.integrations.gitlab import (
    get_gitlab_mrs,
)
from app.tools.GitLabCommitsTool import _gitlab_available, _gl_creds, _resolve_config
from app.tools.tool_decorator import tool


def _list_gitlab_mrs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gl = sources["gitlab"]
    return {
        "project_id": gl["project_id"],
        "updated_after": gl["updated_after"],
        "target_branch": gl.get("target_branch", "main"),
        "per_page": 10,
        **_gl_creds(gl),
    }


def _list_gitlab_mrs_available(sources: dict[str, dict]) -> bool:
    gl = sources.get("gitlab", {})
    return bool(_gitlab_available(sources) and gl.get("project_id"))


@tool(
    name="list_gitlab_mrs",
    source="gitlab",
    description="List recent merge requests for a GitLab project.",
    use_cases=[
        "Checking whether a recently merged MR introduced a failure",
        "Correlating an incident window with recent code merges to the target branch",
        "Identifying open MRs that may have deployed breaking changes",
    ],
    requires=["project_id"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "target_branch": {"type": "string", "default": "main"},
            "updated_after": {"type": "string"},
            "per_page": {"type": "integer", "default": 10},
            "gitlab_url": {"type": "string"},
            "gitlab_token": {"type": "string"},
        },
        "required": ["project_id"],
    },
    is_available=_list_gitlab_mrs_available,
    extract_params=_list_gitlab_mrs_extract_params,
)
def list_gitlab_mrs(
    project_id: str,
    target_branch: str = "main",
    updated_after: str = "",
    per_page: int = 10,
    gitlab_url: str | None = None,
    gitlab_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List recent merge requests for a GitLab project."""
    config = _resolve_config(gitlab_url, gitlab_token)
    if config is None:
        return {
            "source": "gitlab",
            "available": False,
            "error": "gitlab integration is not configured.",
            "mrs": [],
        }

    result = get_gitlab_mrs(
        config=config,
        project_id=project_id,
        target_branch=target_branch,
        updated_after=updated_after,
        per_page=per_page,
    )
    return {"source": "gitlab", "available": True, "mrs": result}
