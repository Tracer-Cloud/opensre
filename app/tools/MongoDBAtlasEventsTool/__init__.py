"""MongoDB Atlas Events Tool."""

from typing import Any

from app.integrations.mongodb_atlas import (
    MongoDBAtlasConfig,
    atlas_extract_params,
    atlas_is_available,
    get_cluster_events,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mongodb_atlas_cluster_events",
    description="Retrieve recent events for a MongoDB Atlas cluster including operational events, configuration changes, and user actions.",
    source="mongodb_atlas",
    surfaces=("investigation", "chat"),
    is_available=atlas_is_available,
    extract_params=atlas_extract_params,
)
def get_mongodb_atlas_cluster_events(
    api_public_key: str,
    api_private_key: str,
    project_id: str,
    cluster_name: str = "",
    base_url: str = "https://cloud.mongodb.com/api/atlas/v2",
    max_results: int = 50,
) -> dict[str, Any]:
    """Fetch recent events from the Atlas Admin API."""
    config = MongoDBAtlasConfig(
        api_public_key=api_public_key,
        api_private_key=api_private_key,
        project_id=project_id,
        base_url=base_url,
        max_results=max_results,
    )
    return get_cluster_events(config, cluster_name)
