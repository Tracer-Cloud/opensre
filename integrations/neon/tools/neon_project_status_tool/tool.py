"""Neon project status: branches, computes, and recent control-plane operations."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from integrations.neon.diagnostics import (
    DEFAULT_OPERATIONS_LIMIT,
    MAX_OPERATIONS_LIMIT,
    get_project_status,
)
from integrations.neon.tools._params import (
    BRANCH_ID_PROPERTY,
    INJECTED_PARAMS,
    INJECTED_SCHEMA_PROPERTIES,
    neon_client,
    neon_extract_params,
    neon_is_available,
)


def map_neon_project_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite compute states and any failed or pending operations."""
    if not output.get("available"):
        return
    project = output.get("project") or {}
    # Both counts cover every compute: ``endpoints`` is capped at max_results.
    total = output.get("total_endpoints", len(output.get("endpoints") or []))
    active = output.get("active_endpoints") or []
    parts = [
        f"{len(active)}/{total} computes active",
        f"{output.get('total_branches', 0)} branches",
    ]
    failed = output.get("failed_operations") or []
    if failed:
        actions = ", ".join(sorted({str(item.get("action", "")) for item in failed}))
        parts.append(f"{len(failed)} failed operations ({actions})")
    pending = output.get("pending_operations") or []
    if pending:
        parts.append(f"{len(pending)} operations in progress")
    disabled = output.get("disabled_endpoints") or []
    if disabled:
        parts.append(f"disabled computes: {', '.join(disabled)}")
    record_evidence_entry(
        evidence,
        source="get_neon_project_status",
        label=f"Neon project {project.get('name') or project.get('id', '')}".strip(),
        summary=", ".join(parts),
    )


class NeonProjectStatusTool(BaseTool):
    """Summarize a Neon project's branches, computes, and recent operations."""

    name = "get_neon_project_status"
    source = "neon"
    surfaces = (ToolSurface.CHAT, ToolSurface.ACTION)
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    evidence_mapper = map_neon_project_status
    description = (
        "Read the configured Neon Postgres project: region and Postgres version, branches "
        "and their state, compute endpoints (active/idle, autoscaling CU limits, "
        "scale-to-zero timeout, last active time), and recent control-plane operations "
        "with failed and in-progress ones listed separately."
    )
    use_cases = [
        "Checking whether a Neon compute is suspended, disabled, or stuck starting during a connection incident",
        "Finding failed or long-running Neon operations (compute restarts, branch resets) near an incident",
        "Confirming autoscaling limits and scale-to-zero settings when latency spikes on cold starts",
        "Listing branches to spot a reset, deleted, or unexpectedly restored branch",
    ]
    requires = ["api_key", "project_id"]
    injected_params = INJECTED_PARAMS
    input_schema = {
        "type": "object",
        "properties": {
            **INJECTED_SCHEMA_PROPERTIES,
            "branch_id": {
                **BRANCH_ID_PROPERTY,
                "description": (
                    "Optional Neon branch ID to narrow computes and operations to one branch. "
                    "Leave empty for the whole project."
                ),
            },
            "operations_limit": {
                "type": "integer",
                "default": DEFAULT_OPERATIONS_LIMIT,
                "minimum": 1,
                "maximum": MAX_OPERATIONS_LIMIT,
                "description": "How many recent operations to read (newest first).",
            },
        },
        "required": ["api_key", "project_id"],
        "additionalProperties": False,
    }
    outputs = {
        "project": "Project region, Postgres version, IP allowlist, and maintenance window",
        "branches": "Branches with state, default/protected flags, and parent",
        "total_endpoints": "Number of compute endpoints before the list is capped",
        "endpoints": "Compute endpoints with state, CU limits, and suspend timeout",
        "failed_operations": "Operations whose status is failed or error",
        "pending_operations": "Operations still running, scheduling, or cancelling",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return neon_is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        params = neon_extract_params(sources)
        # Status covers the whole project unless the model narrows it.
        params["branch_id"] = ""
        params["operations_limit"] = DEFAULT_OPERATIONS_LIMIT
        return params

    def run(
        self,
        api_key: str,
        project_id: str,
        base_url: str = "",
        branch_id: str = "",
        operations_limit: int = DEFAULT_OPERATIONS_LIMIT,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return get_project_status(
            neon_client(api_key, project_id, base_url),
            branch_id=branch_id,
            operations_limit=operations_limit,
        )


get_neon_project_status = NeonProjectStatusTool()
