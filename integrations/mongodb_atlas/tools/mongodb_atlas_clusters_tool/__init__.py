"""MongoDB Atlas Clusters Tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.mongodb_atlas import (
    MongoDBAtlasConfig,
    atlas_extract_params,
    atlas_is_available,
    get_clusters,
)
from integrations.mongodb_atlas.tools._evidence import map_get_mongodb_atlas_clusters


@tool(
    name="get_mongodb_atlas_clusters",
    description="Retrieve all MongoDB Atlas clusters in a project including state, version, instance size, and replication topology.",
    use_cases=[
        "List the clusters in an Atlas project with their state and size",
        "Find the right Atlas cluster before querying its metrics or events",
    ],
    source="mongodb_atlas",
    surfaces=(ToolSurface.CHAT,),
    is_available=atlas_is_available,
    injected_params=("api_private_key", "api_public_key", "base_url"),
    extract_params=atlas_extract_params,
    evidence_mapper=map_get_mongodb_atlas_clusters,
)
def get_mongodb_atlas_clusters(
    api_public_key: str,
    api_private_key: str,
    project_id: str,
    base_url: str = "https://cloud.mongodb.com/api/atlas/v2",
) -> dict[str, Any]:
    """Fetch clusters from the Atlas Admin API."""
    config = MongoDBAtlasConfig(
        api_public_key=api_public_key,
        api_private_key=api_private_key,
        project_id=project_id,
        base_url=base_url,
    )
    return get_clusters(config)
