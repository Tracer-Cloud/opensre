"""List objects in a Neon Object Storage bucket."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from integrations.neon.diagnostics import list_storage_objects
from integrations.neon.tools._params import (
    BRANCH_ID_PROPERTY,
    INJECTED_PARAMS,
    INJECTED_SCHEMA_PROPERTIES,
    neon_client,
    neon_extract_params,
    neon_is_available,
)


def map_neon_storage_objects(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the listed location and how much it holds."""
    if not output.get("available"):
        return
    objects = output.get("objects") or []
    folders = output.get("folders") or []
    summary = f"{len(objects)} objects"
    if folders:
        summary += f", {len(folders)} folders"
    if output.get("truncated"):
        summary += " (more available)"
    newest = max((str(item.get("last_modified", "")) for item in objects), default="")
    if newest:
        summary += f", newest {newest}"
    location = f"{output.get('bucket', '')}/{output.get('prefix', '')}"
    record_evidence_entry(
        evidence,
        source="list_neon_storage_objects",
        label=f"Neon bucket {location}",
        summary=summary,
    )


class NeonStorageObjectsTool(BaseTool):
    """List one page of objects in a Neon Object Storage bucket."""

    name = "list_neon_storage_objects"
    source = "neon"
    surfaces = (ToolSurface.CHAT, ToolSurface.ACTION)
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    evidence_mapper = map_neon_storage_objects
    description = (
        "List objects (key, size, last modified, ETag) in a Neon Object Storage bucket, "
        "optionally under a key prefix. Folders are collapsed on '/' unless recursive is "
        "true. Returns a next_cursor when more results exist."
    )
    use_cases = [
        "Checking whether an expected upload, export, or backup object landed in a Neon bucket",
        "Finding the newest objects under a prefix to correlate with an incident timeline",
        "Browsing a bucket folder by folder to locate artifacts referenced in logs",
    ]
    requires = ["api_key", "project_id"]
    injected_params = INJECTED_PARAMS
    input_schema = {
        "type": "object",
        "properties": {
            **INJECTED_SCHEMA_PROPERTIES,
            "bucket": {"type": "string", "description": "Bucket name on the branch."},
            "branch_id": BRANCH_ID_PROPERTY,
            "prefix": {
                "type": "string",
                "default": "",
                "description": "Only list keys that start with this prefix (e.g. 'logs/2026-11-05/').",
            },
            "recursive": {
                "type": "boolean",
                "default": False,
                "description": "List every key under the prefix instead of collapsing folders.",
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "minimum": 1,
                "maximum": 200,
                "description": "Maximum objects plus folders to return.",
            },
            "cursor": {
                "type": "string",
                "default": "",
                "description": "next_cursor from a previous call, to read the next page.",
            },
        },
        "required": ["api_key", "project_id", "bucket"],
        "additionalProperties": False,
    }
    outputs = {
        "folders": "Collapsed key prefixes ending in '/' (empty when recursive)",
        "objects": "Objects with key, size, last_modified, and etag",
        "truncated": "True when another page exists",
        "next_cursor": "Cursor for the next page, or null",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return neon_is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return neon_extract_params(sources)

    def run(
        self,
        api_key: str,
        project_id: str,
        bucket: str = "",
        base_url: str = "",
        branch_id: str = "",
        prefix: str = "",
        recursive: bool = False,
        limit: int = 50,
        cursor: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return list_storage_objects(
            neon_client(api_key, project_id, base_url),
            bucket=bucket,
            branch_id=branch_id,
            prefix=prefix,
            recursive=recursive,
            limit=limit,
            cursor=cursor,
        )


list_neon_storage_objects = NeonStorageObjectsTool()
