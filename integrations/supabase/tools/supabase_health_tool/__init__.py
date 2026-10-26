"""Supabase Service Health Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import EvidenceType, SideEffectLevel
from core.tool_framework import tool
from integrations.supabase import (
    get_service_health,
    resolve_supabase_config,
    supabase_extract_params,
    supabase_is_available,
)


def _map_get_supabase_service_health(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    services = output.get("services", {})
    if not services:
        return
    degraded = output.get("degraded_services", [])
    summary = f"{len(services)} services checked"
    if degraded:
        summary += f", degraded: {', '.join(degraded)}"
    else:
        summary += ", all healthy"
    record_evidence_entry(
        evidence,
        source="get_supabase_service_health",
        label="Supabase Service Health",
        summary=summary,
    )


@tool(
    name="get_supabase_service_health",
    description="Check the health of all Supabase services (PostgREST, Auth, Storage) for a given project.",
    source="supabase",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Checking Supabase project health during an incident",
        "Identifying which Supabase service (Auth, Storage, PostgREST) is degraded",
        "Triaging intermittent 503 or 401 errors from a Supabase-backed application",
    ],
    is_available=supabase_is_available,
    injected_params=("project_url",),
    extract_params=supabase_extract_params,
    evidence_type=EvidenceType.OTHER,
    evidence_mapper=_map_get_supabase_service_health,
)
def get_supabase_service_health(
    project_url: str,
) -> dict[str, Any]:
    """Fetch health status for all services in a Supabase project."""
    config = resolve_supabase_config(project_url)
    return get_service_health(config)
