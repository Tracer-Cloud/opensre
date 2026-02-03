"""Service map summaries for memory embeddings."""

from .types import ServiceMap


def get_compact_asset_inventory(service_map: ServiceMap, limit: int = 10) -> str:
    """Get compact asset inventory summary for memory embedding."""
    if not service_map["enabled"] or not service_map["assets"]:
        return "No assets discovered."

    # Sort by investigation_count (hotspots first)
    sorted_assets = sorted(
        service_map["assets"],
        key=lambda a: a.get("investigation_count", 0),
        reverse=True,
    )

    lines = []
    for asset in sorted_assets[:limit]:
        count = asset.get("investigation_count", 1)
        confidence = asset.get("confidence", 1.0)
        # Add marker for tentative assets
        if asset.get("verification_status") == "needs_verification":
            status_marker = "?"
        else:
            status_marker = ""
        lines.append(
            f"- {asset['type']}: {asset['name']} "
            f"(investigated {count}x, confidence={confidence:.1f}){status_marker}"
        )

    if len(sorted_assets) > limit:
        lines.append(f"... +{len(sorted_assets) - limit} more assets")

    return "\n".join(lines)
