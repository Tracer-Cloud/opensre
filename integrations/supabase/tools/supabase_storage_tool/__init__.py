"""Supabase Storage Buckets Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import EvidenceType, SideEffectLevel
from core.tool_framework import tool
from integrations.supabase import (
    get_storage_buckets,
    resolve_supabase_config,
    supabase_extract_params,
    supabase_is_available,
)


def _map_get_supabase_storage_buckets(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    buckets = output.get("buckets", [])
    if not buckets:
        return
    total = output.get("total_buckets", len(buckets))
    summary = f"{len(buckets)} buckets"
    if output.get("truncated"):
        summary += f" (of {total})"
    public = [b.get("name", "") for b in buckets if b.get("public")]
    if public:
        summary += f", public: {', '.join(n for n in public if n)}"
    record_evidence_entry(
        evidence,
        source="get_supabase_storage_buckets",
        label="Supabase Storage Buckets",
        summary=summary,
    )


@tool(
    name="get_supabase_storage_buckets",
    description="List all Supabase Storage buckets and their configuration metadata.",
    source="supabase",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Auditing storage bucket configuration during a file upload incident",
        "Checking whether a bucket is public or private when debugging access errors",
        "Listing all buckets to identify orphaned or misconfigured storage resources",
    ],
    is_available=supabase_is_available,
    injected_params=("project_url",),
    extract_params=supabase_extract_params,
    evidence_type=EvidenceType.OTHER,
    evidence_mapper=_map_get_supabase_storage_buckets,
)
def get_supabase_storage_buckets(
    project_url: str,
) -> dict[str, Any]:
    """List all storage buckets in a Supabase project."""
    config = resolve_supabase_config(project_url)
    return get_storage_buckets(config)
