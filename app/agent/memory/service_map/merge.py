"""Merging logic for service maps."""

from datetime import UTC, datetime

from .storage import load_service_map
from .types import Asset, Edge, HistoryEntry, ServiceMap


def merge_with_existing_map(
    new_map: ServiceMap, pipeline_name: str, alert_name: str
) -> ServiceMap:
    """Merge new assets/edges with existing service map, updating hotspots and history."""
    existing_map = load_service_map()
    if not existing_map["enabled"]:
        return new_map

    # Build lookups
    existing_assets_by_id = {asset["id"]: asset for asset in existing_map["assets"]}
    existing_edges_by_key = {
        (edge["from_asset"], edge["to_asset"], edge["type"]): edge
        for edge in existing_map["edges"]
    }

    merged_assets: list[Asset] = []
    merged_edges: list[Edge] = []
    history: list[HistoryEntry] = list(existing_map.get("history", []))
    now = datetime.now(UTC).isoformat()

    # Merge assets (update hotspots)
    for new_asset in new_map["assets"]:
        asset_id = new_asset["id"]
        if asset_id in existing_assets_by_id:
            # Update existing asset
            existing_asset = existing_assets_by_id[asset_id]
            existing_asset["investigation_count"] = existing_asset.get("investigation_count", 0) + 1
            existing_asset["last_investigated"] = now

            # Merge pipeline context
            if pipeline_name and pipeline_name not in existing_asset.get("pipeline_context", []):
                existing_asset.setdefault("pipeline_context", []).append(pipeline_name)

            # Merge alert context
            if alert_name and alert_name not in existing_asset.get("alert_context", []):
                existing_asset.setdefault("alert_context", []).append(alert_name)

            # Upgrade confidence if tentative becomes verified
            if (
                existing_asset.get("verification_status") == "needs_verification"
                and new_asset.get("verification_status") == "verified"
            ):
                existing_asset["verification_status"] = "verified"
                existing_asset["confidence"] = new_asset["confidence"]
                history.append(
                    {
                        "timestamp": now,
                        "change_type": "asset_verified",
                        "asset_id": asset_id,
                        "edge_id": None,
                        "details": f"Asset {asset_id} verified",
                    }
                )

            merged_assets.append(existing_asset)
            existing_assets_by_id.pop(asset_id)
        else:
            # New asset
            merged_assets.append(new_asset)
            history.append(
                {
                    "timestamp": now,
                    "change_type": "asset_added",
                    "asset_id": asset_id,
                    "edge_id": None,
                    "details": f"New asset: {new_asset['type']} {new_asset['name']}",
                }
            )

    # Add remaining existing assets
    merged_assets.extend(existing_assets_by_id.values())

    # Merge edges
    for new_edge in new_map["edges"]:
        edge_key = (new_edge["from_asset"], new_edge["to_asset"], new_edge["type"])
        if edge_key in existing_edges_by_key:
            # Update existing edge
            existing_edge = existing_edges_by_key[edge_key]
            existing_edge["last_seen"] = now

            # Upgrade confidence if tentative becomes verified
            if (
                existing_edge.get("verification_status") == "needs_verification"
                and new_edge.get("verification_status") == "verified"
            ):
                existing_edge["verification_status"] = "verified"
                existing_edge["confidence"] = new_edge["confidence"]
                edge_id = f"{edge_key[0]}→{edge_key[1]}"
                history.append(
                    {
                        "timestamp": now,
                        "change_type": "edge_verified",
                        "asset_id": None,
                        "edge_id": edge_id,
                        "details": f"Edge {edge_id} verified",
                    }
                )

            merged_edges.append(existing_edge)
            existing_edges_by_key.pop(edge_key)
        else:
            # New edge
            merged_edges.append(new_edge)
            edge_id = f"{new_edge['from_asset']}→{new_edge['to_asset']}"
            history.append(
                {
                    "timestamp": now,
                    "change_type": "edge_added",
                    "asset_id": None,
                    "edge_id": edge_id,
                    "details": f"New edge: {new_edge['type']}",
                }
            )

    # Add remaining existing edges
    merged_edges.extend(existing_edges_by_key.values())

    # Keep only last 20 history entries
    history = history[-20:]

    return {
        "enabled": True,
        "last_updated": now,
        "assets": merged_assets,
        "edges": merged_edges,
        "history": history,
    }
