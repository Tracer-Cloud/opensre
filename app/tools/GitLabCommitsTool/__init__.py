"""gitlab repository investigation tools."""

from __future__ import annotations

from typing import Any

from app.integrations.gitlab import (
    GitlabConfig,
    build_gitlab_config,
    get_gitlab_commits,
    gitlab_config_from_env,
)
from app.tools.tool_decorator import tool


def _gl_creds(gl: dict) -> dict:
    return {
        "gitlab_url": gl.get("gitlab_url"),
        "gitlab_token": gl.get("gitlab_token"),
    }

def _gitlab_available(sources: dict) -> bool:
    return bool(sources.get("gitlab", {}).get("connection_verified"))

def _resolve_config(
    gitlab_url: str | None,
    gitlab_token: str | None
) -> GitlabConfig | None:
    env_config = gitlab_config_from_env()
    if any([gitlab_url, gitlab_token]):
        return build_gitlab_config({
            "base_url": gitlab_url or (env_config.base_url if env_config else ""),
            "auth_token": gitlab_token or (env_config.auth_token if env_config else ""),
        })
    return env_config

def _list_gitlab_commits_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gl = sources["gitlab"]
    return {
        "project_id": gl["project_id"],
        "since": gl.get("since", ""),
        "ref_name": gl.get("ref_name", "main"),
        "per_page": 10,
        **_gl_creds(gl),
    }


def _list_gitlab_commits_available(sources: dict[str, dict]) -> bool:
    gl = sources.get("gitlab", {})
    return bool(_gitlab_available(sources) and gl.get("project_id"))


@tool(
    name="list_gitlab_commits",
    source="gitlab",
    description="List recent commits for a gitlab repository.",
    use_cases=[
        "Checking whether a recent change could explain a failure",
        "Correlating a deployment or incident window with code changes",
    ],
    requires=["project_id"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "ref_name": {"type": "string", "default": ""},
            "since": {"type": "string"},
            "per_page": {"type": "integer", "default": 10},
            "gitlab_url": {"type": "string"},
            "gitlab_token": {"type": "string"},
        },
        "required": ["project_id"],
    },
    is_available=_list_gitlab_commits_available,
    extract_params=_list_gitlab_commits_extract_params,
)
def list_gitlab_commits(
    project_id: str,
    ref_name: str = "main",
    since: str = "",
    per_page: int = 10,
    gitlab_url: str | None = None,
    gitlab_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List recent commits for a Gitlab repository"""
    config = _resolve_config(gitlab_url,  gitlab_token)
    if config is None:
        return {"source": "gitlab", "available": False, "error": "gitlab integration is not configured.", "commits": []}

    result = get_gitlab_commits(
        config=config,
        project_id=project_id,
        ref_name=ref_name,
        since=since,
        per_page=per_page
    )
    return {"source": "gitlab", "available": True, "commits": result}
