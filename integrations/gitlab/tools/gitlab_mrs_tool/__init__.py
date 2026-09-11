"""gitlab repository investigation tools."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import code_host_unavailable_payload
from integrations.gitlab import (
    get_gitlab_mrs,
)
from integrations.gitlab.tools.gitlab_commits_tool import (
    _GITLAB_REPOSITORY_CONTEXT_PARAMS,
    _gitlab_available,
    _gitlab_count_label,
    _gl_creds,
    _resolve_config,
)


def _map_list_gitlab_mrs(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the number of merge requests retrieved and the target branch."""
    if not output.get("available"):
        return
    mrs = output.get("mrs") or []
    if not mrs:
        return
    label = _gitlab_count_label(len(mrs), tool_input.get("per_page", 10))
    target_branch = tool_input.get("target_branch", "main")
    record_evidence_entry(
        evidence,
        source="list_gitlab_mrs",
        label="GitLab Merge Requests",
        summary=f"{label} merge request(s) targeting '{target_branch}'",
    )


def _list_gitlab_mrs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gl = sources.get("gitlab", {})
    return {
        "project_id": gl.get("project_id"),
        "updated_after": gl.get("updated_after", ""),
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
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "target_branch": {"type": "string", "default": "main"},
            "updated_after": {"type": "string"},
            "per_page": {"type": "integer", "default": 10},
        },
        "required": ["project_id"],
    },
    is_available=_list_gitlab_mrs_available,
    is_advertised=_gitlab_available,
    extract_params=_list_gitlab_mrs_extract_params,
    context_params=_GITLAB_REPOSITORY_CONTEXT_PARAMS,
    evidence_mapper=_map_list_gitlab_mrs,
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
        return code_host_unavailable_payload(
            source="gitlab",
            integration_name="gitlab",
            empty_key="mrs",
            empty_value=[],
        )

    result = get_gitlab_mrs(
        config=config,
        project_id=project_id,
        target_branch=target_branch,
        updated_after=updated_after,
        per_page=per_page,
    )
    return {"source": "gitlab", "available": True, "mrs": result}
