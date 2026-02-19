"""Pipeline dependency graph context source.

Loads the service map, finds upstream pipelines connected via 'feeds_into' edges,
then queries the Tracer API for their recent run status. When an upstream pipeline
has failed within the last 2 hours, sets causal_chain_detected=True so the
planner and diagnosis node can use that as a strong hypothesis.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.agent.memory.service_map import load_service_map
from app.agent.memory.service_map.config import is_service_map_enabled
from app.agent.memory.service_map.identifiers import generate_asset_id
from app.agent.nodes.build_context.context_building import ContextSourceResult
from app.agent.nodes.build_context.utils import call_safe
from app.agent.state import InvestigationState
from app.agent.tools.clients.tracer_client import get_tracer_client_for_org

_CAUSAL_WINDOW_MINUTES = 120  # upstream failure must be within 2h to count


def build_context_dependency(state: InvestigationState) -> ContextSourceResult:
    """Load the dependency graph and detect upstream pipeline failures."""

    empty = ContextSourceResult(
        data={"upstream_pipelines": [], "causal_chain_detected": False, "causal_chain_confidence": 0.0}
    )

    if not is_service_map_enabled():
        return empty

    pipeline_name = state.get("pipeline_name") or ""
    if not pipeline_name:
        return empty

    outcome = call_safe(load_service_map, timeout=5.0)
    if outcome.error or not outcome.result:
        return empty

    service_map = outcome.result
    if not service_map.get("enabled") or not service_map.get("edges"):
        return empty

    pipeline_id = generate_asset_id("pipeline", pipeline_name)

    # Find upstream pipelines connected by feeds_into edges
    upstream_ids = [
        edge["from_asset"]
        for edge in service_map["edges"]
        if edge.get("type") == "feeds_into" and edge.get("to_asset") == pipeline_id
    ]

    if not upstream_ids:
        return empty

    # Extract names from asset IDs ("pipeline:name" → "name")
    upstream_names: list[str] = []
    for asset_id in upstream_ids:
        parts = asset_id.split(":", 1)
        if len(parts) == 2 and parts[0] == "pipeline":
            upstream_names.append(parts[1])

    if not upstream_names:
        return empty

    # Find shared assets for context
    shared_assets_by_pipeline: dict[str, str] = {}
    for edge in service_map["edges"]:
        if edge.get("type") == "feeds_into" and edge.get("to_asset") == pipeline_id:
            evidence_str = edge.get("evidence", "")
            if "shared_s3_asset:" in evidence_str:
                asset_id_from_evidence = evidence_str.split("shared_s3_asset:", 1)[1]
                upstream_name = edge["from_asset"].split(":", 1)[-1]
                shared_assets_by_pipeline[upstream_name] = asset_id_from_evidence

    # Query Tracer API for each upstream pipeline's recent run status
    org_id = state.get("org_id", "")
    auth_token = state.get("_auth_token", "")

    upstream_pipelines: list[dict] = []
    causal_chain_detected = False

    for name in upstream_names:
        shared_asset = shared_assets_by_pipeline.get(name, "")
        run_info = _get_upstream_status(name, org_id, auth_token, shared_asset)
        if run_info:
            upstream_pipelines.append(run_info)
            if (
                run_info.get("status") in ("failed", "error", "Failed", "Error")
                and run_info.get("minutes_ago", 9999) <= _CAUSAL_WINDOW_MINUTES
            ):
                causal_chain_detected = True

    confidence = 0.85 if causal_chain_detected else 0.0

    return ContextSourceResult(
        data={
            "upstream_pipelines": upstream_pipelines,
            "causal_chain_detected": causal_chain_detected,
            "causal_chain_confidence": confidence,
        }
    )


def _get_upstream_status(
    pipeline_name: str,
    org_id: str,
    auth_token: str,
    shared_asset: str,
) -> dict | None:
    """Query Tracer API for a pipeline's most recent run status."""
    if not org_id or not auth_token:
        return None

    try:
        client = get_tracer_client_for_org(org_id, auth_token)
        runs = client.get_pipeline_runs(pipeline_name, size=1)
        if not runs:
            return None

        run = runs[0]
        minutes_ago = _minutes_ago(run.end_time or run.start_time)

        return {
            "name": pipeline_name,
            "status": run.status,
            "failed_at": run.end_time or run.start_time,
            "minutes_ago": minutes_ago,
            "shared_asset": shared_asset,
        }
    except Exception:
        return None


def _minutes_ago(timestamp: str | None) -> int:
    """Return minutes elapsed since ISO timestamp, or 9999 if unparseable."""
    if not timestamp:
        return 9999
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        delta = datetime.now(UTC) - ts
        return max(0, int(delta.total_seconds() / 60))
    except (ValueError, TypeError):
        return 9999
