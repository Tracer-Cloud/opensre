"""gitlab repository investigation tools."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import code_host_unavailable_payload
from integrations.gitlab import (
    get_gitlab_pipelines,
)
from integrations.gitlab.tools.gitlab_commits_tool import (
    _gitlab_available,
    _gitlab_count_label,
    _gl_creds,
    _resolve_config,
)


def _map_list_gitlab_pipelines(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the number of pipelines retrieved and the status filter used."""
    if not output.get("available"):
        return
    pipelines = output.get("pipelines") or []
    if not pipelines:
        return
    label = _gitlab_count_label(len(pipelines), tool_input.get("per_page", 10))
    status = tool_input.get("status", "failed")
    record_evidence_entry(
        evidence,
        source="list_gitlab_pipelines",
        label="GitLab Pipelines",
        summary=f"{label} pipeline(s) with status '{status}'",
    )


def _list_gitlab_pipelines_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gl = sources.get("gitlab", {})
    return {
        "project_id": gl.get("project_id"),
        "updated_after": gl.get("updated_after", ""),
        "ref": gl.get("ref_name", "main"),
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
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "ref": {"type": "string", "default": "main"},
            "updated_after": {"type": "string"},
            "status": {"type": "string", "default": "failed"},
            "per_page": {"type": "integer", "default": 10},
        },
        "required": ["project_id"],
    },
    is_available=_list_gitlab_pipelines_available,
    is_advertised=_gitlab_available,
    extract_params=_list_gitlab_pipelines_extract_params,
    evidence_mapper=_map_list_gitlab_pipelines,
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
        return code_host_unavailable_payload(
            source="gitlab",
            integration_name="gitlab",
            empty_key="pipelines",
            empty_value=[],
        )

    result = get_gitlab_pipelines(
        config=config,
        project_id=project_id,
        ref=ref,
        status=status,
        updated_after=updated_after,
        per_page=per_page,
    )
    return {"source": "gitlab", "available": True, "pipelines": result}
