"""gitlab repository investigation tools."""

from __future__ import annotations

import base64
from typing import Any

from app.integrations.gitlab import (
    get_gitlab_file,
)
from app.tools.GitLabCommitsTool import _gitlab_available, _gl_creds, _resolve_config
from app.tools.tool_decorator import tool
from app.tools.utils.code_host_unavailable import code_host_unavailable_payload


def _get_gitlab_file_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gl = sources["gitlab"]
    return {
        "project_id": gl["project_id"],
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
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "file_path": {"type": "string"},
            "ref": {"type": "string", "default": "main"},
            "gitlab_url": {"type": "string"},
            "gitlab_token": {"type": "string"},
        },
        "required": ["project_id", "file_path"],
    },
    is_available=_get_gitlab_file_available,
    extract_params=_get_gitlab_file_extract_params,
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
        return {
            "source": "gitlab",
            "available": False,
            "error": f"File too large to read ({result['size']} bytes)",
            "file": {},
        }

    content_raw = result.get("content", "")
    if content_raw:
        try:
            content_decoded = base64.b64decode(content_raw).decode("utf-8")
        except UnicodeDecodeError:
            return {
                "source": "gitlab",
                "available": False,
                "error": f"File '{file_path}' is not UTF-8 text (binary file); cannot display contents.",
                "file": {},
            }
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
