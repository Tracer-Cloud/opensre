"""Service map build orchestration."""

from datetime import UTC, datetime
from typing import Any

from .assets import (
    ensure_edge_endpoint_assets,
    extract_assets_from_infrastructure,
    infer_tentative_assets_from_alert,
)
from .config import is_service_map_enabled
from .edges import dedupe_edges, extract_edges_from_evidence, infer_topology_edges
from .merge import merge_with_existing_map
from .types import ServiceMap


def build_service_map(
    evidence: dict[str, Any],
    raw_alert: dict[str, Any],
    context: dict[str, Any],
    pipeline_name: str,
    alert_name: str,
) -> ServiceMap:
    """Build service map from investigation evidence and alert data."""
    if not is_service_map_enabled():
        return {
            "enabled": False,
            "last_updated": datetime.now(UTC).isoformat(),
            "assets": [],
            "edges": [],
            "history": [],
        }

    # Step 1: Extract edges directly from evidence (evidence-first approach)
    edges_from_evidence = extract_edges_from_evidence(evidence, raw_alert, pipeline_name)

    # Step 2: Ensure edge endpoints exist as assets
    assets_from_edges = ensure_edge_endpoint_assets(
        edges_from_evidence, evidence, pipeline_name, alert_name
    )

    # Step 3: Extract assets from infrastructure (adds remaining assets + enriches metadata)
    ctx_for_extraction = {
        "evidence": evidence,
        "raw_alert": raw_alert,
        "context": context,
    }
    assets_from_infra = extract_assets_from_infrastructure(
        ctx_for_extraction, pipeline_name, alert_name
    )

    # Merge assets (prefer infrastructure extraction for metadata)
    assets_by_id = {a["id"]: a for a in assets_from_infra}
    for edge_asset in assets_from_edges:
        if edge_asset["id"] not in assets_by_id:
            assets_by_id[edge_asset["id"]] = edge_asset

    assets = list(assets_by_id.values())

    # Step 4: Infer additional edges from asset topology
    edges_from_topology = infer_topology_edges(assets)

    # Combine all edges (deduplicate by key)
    edges = dedupe_edges(edges_from_evidence + edges_from_topology)

    # Step 5: Infer tentative assets/edges from alert context
    tentative_assets, tentative_edges = infer_tentative_assets_from_alert(
        alert_name, raw_alert, assets
    )
    assets.extend(tentative_assets)
    edges.extend(tentative_edges)

    # Build new map
    new_map: ServiceMap = {
        "enabled": True,
        "last_updated": datetime.now(UTC).isoformat(),
        "assets": assets,
        "edges": edges,
        "history": [],
    }

    # Merge with existing map (hotspots + history)
    return merge_with_existing_map(new_map, pipeline_name, alert_name)
