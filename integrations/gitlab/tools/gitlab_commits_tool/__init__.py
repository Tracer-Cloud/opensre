"""gitlab repository investigation tools."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import code_host_unavailable_payload
from integrations.gitlab import (
    GitlabConfig,
    build_gitlab_config,
    get_gitlab_commits,
    gitlab_config_from_env,
)


#: Every GitLab list tool (commits/pipelines/MRs) sends ``per_page`` straight
#: to the GitLab API as its own page-size param and returns the raw page with
#: no separate total-count field -- a returned page cannot be distinguished
#: from every matching record except by comparing it against the page size
#: that was requested.
def _gitlab_count_label(count: int, requested_per_page: int) -> str:
    """Format a list count, appending "+" when the page may be truncated."""
    effective_per_page = max(requested_per_page, 1)
    return f"{count}+" if count >= effective_per_page else str(count)


def _clean_optional(value: str | None) -> str:
    return str(value or "").strip()


def _gl_creds(gl: dict) -> dict:
    """Pull resolved integration credentials out of the ``gitlab`` source dict.

    The integration resolution pipeline loads the configured (keyring-backed)
    token/url into ``sources["gitlab"]`` under ``base_url``/``auth_token``; the
    legacy ``gitlab_url``/``gitlab_token`` keys are accepted for back-compat.
    """
    return {
        "gitlab_url": gl.get("gitlab_url") or gl.get("base_url"),
        "gitlab_token": gl.get("gitlab_token") or gl.get("auth_token"),
    }


def _gitlab_available(sources: dict) -> bool:
    return bool(sources.get("gitlab", {}).get("connection_verified"))


def _resolve_config(gitlab_url: str | None, gitlab_token: str | None) -> GitlabConfig | None:
    """Resolve a GitLab config from injected (resolved integration) credentials.

    Falls back to env config only when no resolved credentials were injected.
    An empty token with a present URL returns ``None`` (unavailable) instead of
    building a config that would emit an invalid ``Bearer `` header.
    """
    env_config = gitlab_config_from_env()
    base_url = _clean_optional(gitlab_url)
    auth_token = _clean_optional(gitlab_token)
    if base_url or auth_token:
        if not auth_token:
            return None
        return build_gitlab_config(
            {
                "base_url": base_url or (env_config.base_url if env_config else ""),
                "auth_token": auth_token,
            }
        )
    return env_config


def _list_gitlab_commits_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gl = sources.get("gitlab", {})
    return {
        "project_id": gl.get("project_id"),
        "since": gl.get("since", ""),
        "ref_name": gl.get("ref_name", "main"),
        "per_page": 10,
        **_gl_creds(gl),
    }


def _list_gitlab_commits_available(sources: dict[str, dict]) -> bool:
    gl = sources.get("gitlab", {})
    return bool(_gitlab_available(sources) and gl.get("project_id"))


def _map_list_gitlab_commits(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the number of commits retrieved."""
    if not output.get("available"):
        return
    commits = output.get("commits") or []
    if not commits:
        return
    label = _gitlab_count_label(len(commits), tool_input.get("per_page", 10))
    record_evidence_entry(
        evidence,
        source="list_gitlab_commits",
        label="GitLab Commits",
        summary=f"{label} commit(s)",
    )


@tool(
    name="list_gitlab_commits",
    source="gitlab",
    description="List recent commits for a gitlab repository.",
    use_cases=[
        "Checking whether a recent change could explain a failure",
        "Correlating a deployment or incident window with code changes",
    ],
    requires=["project_id"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "ref_name": {"type": "string", "default": ""},
            "since": {"type": "string"},
            "per_page": {"type": "integer", "default": 10},
        },
        "required": ["project_id"],
    },
    is_available=_list_gitlab_commits_available,
    is_advertised=_gitlab_available,
    extract_params=_list_gitlab_commits_extract_params,
    evidence_mapper=_map_list_gitlab_commits,
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
    config = _resolve_config(gitlab_url, gitlab_token)
    if config is None:
        return code_host_unavailable_payload(
            source="gitlab",
            integration_name="gitlab",
            empty_key="commits",
            empty_value=[],
        )

    result = get_gitlab_commits(
        config=config, project_id=project_id, ref_name=ref_name, since=since, per_page=per_page
    )
    return {"source": "gitlab", "available": True, "commits": result}
