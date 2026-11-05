"""Neon Object Storage buckets for one branch."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from integrations.neon.diagnostics import get_storage_buckets
from integrations.neon.tools._params import (
    BRANCH_ID_PROPERTY,
    INJECTED_PARAMS,
    INJECTED_SCHEMA_PROPERTIES,
    neon_client,
    neon_extract_params,
    neon_is_available,
)


def map_neon_storage_buckets(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite whether storage is on, the bucket count, and any public buckets."""
    if not output.get("available"):
        return
    if not output.get("storage_enabled"):
        summary = "Object Storage is not enabled on this branch"
    else:
        summary = f"{output.get('total_buckets', 0)} buckets"
        public = output.get("public_buckets") or []
        if public:
            summary += f", public: {', '.join(public)}"
    record_evidence_entry(
        evidence,
        source="get_neon_storage_buckets",
        label=f"Neon Object Storage ({output.get('branch_id', '')})",
        summary=summary,
        url=output.get("s3_endpoint") or None,
    )


class NeonStorageBucketsTool(BaseTool):
    """List Neon Object Storage buckets and the branch's S3 endpoint."""

    name = "get_neon_storage_buckets"
    source = "neon"
    surfaces = (ToolSurface.CHAT, ToolSurface.ACTION)
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    evidence_mapper = map_neon_storage_buckets
    description = (
        "List the Neon Object Storage buckets on a branch with their access level "
        "(private or public_read), plus whether storage is enabled and the branch's "
        "S3-compatible endpoint and region."
    )
    use_cases = [
        "Checking whether a bucket an app uploads to exists on the branch it is deployed against",
        "Auditing which Neon buckets are publicly readable after an access or data-exposure report",
        "Getting the branch's S3 endpoint to compare with an app's AWS_ENDPOINT_URL_S3",
    ]
    requires = ["api_key", "project_id"]
    injected_params = INJECTED_PARAMS
    input_schema = {
        "type": "object",
        "properties": {
            **INJECTED_SCHEMA_PROPERTIES,
            "branch_id": BRANCH_ID_PROPERTY,
        },
        "required": ["api_key", "project_id"],
        "additionalProperties": False,
    }
    outputs = {
        "storage_enabled": "Whether Object Storage is enabled on the branch",
        "s3_endpoint": "The branch's S3-compatible endpoint (path-style)",
        "buckets": "Buckets with access level and creation time",
        "public_buckets": "Names of buckets that are not private",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return neon_is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return neon_extract_params(sources)

    def run(
        self,
        api_key: str,
        project_id: str,
        base_url: str = "",
        branch_id: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return get_storage_buckets(neon_client(api_key, project_id, base_url), branch_id=branch_id)


get_neon_storage_buckets = NeonStorageBucketsTool()
