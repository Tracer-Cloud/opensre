"""gitlab repository investigation tools."""

from __future__ import annotations

import base64
from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import code_host_unavailable_payload, tool_unavailable
from integrations.gitlab import (
    get_gitlab_file,
)
from integrations.gitlab.tools.gitlab_commits_tool import (
    _gitlab_available,
    _gl_creds,
    _resolve_config,
)


def _map_get_gitlab_file_contents(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the file path, ref, and line count read."""
    if not output.get("available"):
        return
    file = output.get("file") or {}
    if not file:
        return
    content = file.get("content") or ""
    line_count = content.count("\n") + (not content.endswith("\n")) if content else 0
    record_evidence_entry(
        evidence,
        source="get_gitlab_file",
        label="GitLab File",
        summary=(f"'{file.get('file_path', '')}' @ {file.get('ref', '')}, {line_count} line(s)"),
    )


def _get_gitlab_file_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gl = sources.get("gitlab", {})
    return {
        "project_id": gl.get("project_id"),
        "file_path": gl.get("file_path", ""),
        "ref": gl.get("ref_name", "main"),
        **_gl_creds(gl),
    }


def _get_gitlab_file_available(sources: dict[str, dict]) -> bool:
    gl = sources.get("gitlab", {})
    return bool(_gitlab_available(sources) and gl.get("project_id") and gl.get("file_path"))


@tool(
    name="get_gitlab_file",
    source="gitlab",
    description="Read the contents of a specific file from a GitLab repository.",
    use_cases=[
        "Reading a config file that may explain a misconfiguration causing the incident",
        "Inspecting a schema or manifest file referenced in the alert error message",
        "Viewing a specific version of a file at the deployed commit or branch",
    ],
    requires=["project_id", "file_path"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "file_path": {"type": "string"},
            "ref": {"type": "string", "default": "main"},
        },
        "required": ["project_id", "file_path"],
    },
    is_available=_get_gitlab_file_available,
    is_advertised=_gitlab_available,
    extract_params=_get_gitlab_file_extract_params,
    evidence_mapper=_map_get_gitlab_file_contents,
)
def get_gitlab_file_contents(
    project_id: str,
    file_path: str,
    ref: str = "main",
    gitlab_url: str | None = None,
    gitlab_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Read the contents of a specific file from a GitLab repository."""
    config = _resolve_config(gitlab_url, gitlab_token)
    if config is None:
        return code_host_unavailable_payload(
            source="gitlab",
            integration_name="gitlab",
            empty_key="file",
            empty_value={},
        )

    result = get_gitlab_file(
        config=config,
        project_id=project_id,
        ref=ref,
        file_path=file_path,
    )
    if result.get("size", 0) > 50_000:
        return tool_unavailable(
            "gitlab", f"File too large to read ({result['size']} bytes)", file={}
        )

    content_raw = result.get("content", "")
    if content_raw:
        try:
            content_decoded = base64.b64decode(content_raw).decode("utf-8")
        except UnicodeDecodeError:
            return tool_unavailable(
                "gitlab",
                f"File '{file_path}' is not UTF-8 text (binary file); cannot display contents.",
                file={},
            )
    else:
        content_decoded = ""

    return {
        "source": "gitlab",
        "available": True,
        "file": {
            "file_name": result.get("file_name"),
            "file_path": result.get("file_path"),
            "ref": result.get("ref"),
            "content": content_decoded,
        },
    }
