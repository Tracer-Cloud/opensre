"""gitlab repository investigation tools."""

from __future__ import annotations

from typing import Any

from app.integrations.gitlab import (
    get_gitlab_pipelines,
)
from app.tools.GitLabCommitsTool import _gitlab_available, _gl_creds, _resolve_config
from app.tools.tool_decorator import tool


def _list_gitlab_pipelines_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gl = sources["gitlab"]
    return {
        "project_id": gl["project_id"],
        "updated_after": gl["updated_after"],
        "ref": gl.get("ref", "main"),
        "status": "failed",
        "per_page": 10,
        **_gl_creds(gl),
    }


def _list_gitlab_pipelines_available(sources: dict[str, dict]) -> bool:
    gl = sources.get("gitlab", {})
    return bool(_gitlab_available(sources) and gl.get("project_id"))


@tool(
    name="list_gitlab_pipelines",
    source="gitlab",
    description="List recent CI/CD pipelines for a GitLab project.",
    use_cases=[
        "Checking whether a failed pipeline caused or coincided with the incident",
        "Correlating a deployment window with a pipeline that ran around the alert time",
        "Identifying which CI job failed and on which branch",
    ],
    requires=["project_id"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "ref": {"type": "string", "default": "main"},
            "updated_after": {"type": "string"},
            "status": {"type": "string", "default": "failed"},
            "per_page": {"type": "integer", "default": 10},
            "gitlab_url": {"type": "string"},
            "gitlab_token": {"type": "string"},
        },
        "required": ["project_id"],
    },
    is_available=_list_gitlab_pipelines_available,
    extract_params=_list_gitlab_pipelines_extract_params,
)
def list_gitlab_pipelines(
    project_id: str,
    ref: str = "main",
    updated_after: str = "",
    status: str = "failed",
    per_page: int = 10,
    gitlab_url: str | None = None,
    gitlab_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List recent CI/CD pipelines for a GitLab project."""
    config = _resolve_config(gitlab_url, gitlab_token)
    if config is None:
        return {
            "source": "gitlab",
            "available": False,
            "error": "gitlab integration is not configured.",
            "pipelines": [],
        }

    result = get_gitlab_pipelines(
        config=config,
        project_id=project_id,
        ref=ref,
        status=status,
        updated_after=updated_after,
        per_page=per_page,
    )
    return {"source": "gitlab", "available": True, "pipelines": result}
