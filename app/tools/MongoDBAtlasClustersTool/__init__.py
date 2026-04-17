"""MongoDB Atlas Clusters Tool."""

from typing import Any

from app.integrations.mongodb_atlas import (
    MongoDBAtlasConfig,
    atlas_extract_params,
    atlas_is_available,
    get_clusters,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mongodb_atlas_clusters",
    description="Retrieve all MongoDB Atlas clusters in a project including state, version, instance size, and replication topology.",
    source="mongodb_atlas",
    surfaces=("investigation", "chat"),
    is_available=atlas_is_available,
    extract_params=atlas_extract_params,
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
