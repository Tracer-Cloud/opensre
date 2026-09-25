"""MongoDB Atlas Alerts Tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.mongodb_atlas import (
    MongoDBAtlasConfig,
    atlas_extract_params,
    atlas_is_available,
    get_alerts,
)
from integrations.mongodb_atlas.tools._evidence import map_get_mongodb_atlas_alerts


@tool(
    name="get_mongodb_atlas_alerts",
    description="Retrieve open alerts for a MongoDB Atlas project including event type, metric, cluster, and current value.",
    use_cases=[
        "See which alerts are open for a MongoDB Atlas project",
    ],
    source="mongodb_atlas",
    surfaces=(ToolSurface.CHAT,),
    is_available=atlas_is_available,
    injected_params=("api_private_key", "api_public_key", "base_url"),
    extract_params=atlas_extract_params,
    evidence_mapper=map_get_mongodb_atlas_alerts,
)
def get_mongodb_atlas_alerts(
    api_public_key: str,
    api_private_key: str,
    project_id: str,
    base_url: str = "https://cloud.mongodb.com/api/atlas/v2",
    max_results: int = 50,
) -> dict[str, Any]:
    """Fetch open alerts from the Atlas Admin API."""
    config = MongoDBAtlasConfig(
        api_public_key=api_public_key,
        api_private_key=api_private_key,
        project_id=project_id,
        base_url=base_url,
        max_results=max_results,
    )
    return get_alerts(config)
