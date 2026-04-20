"""MongoDB Atlas Performance Advisor Tool."""

from typing import Any

from app.integrations.mongodb_atlas import (
    MongoDBAtlasConfig,
    atlas_extract_params,
    atlas_is_available,
    get_performance_advisor,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mongodb_atlas_performance_advisor",
    description="Retrieve Performance Advisor suggestions for a MongoDB Atlas cluster including recommended indexes and slow query logs.",
    source="mongodb_atlas",
    surfaces=("investigation", "chat"),
    is_available=atlas_is_available,
    extract_params=atlas_extract_params,
)
def get_mongodb_atlas_performance_advisor(
    api_public_key: str,
    api_private_key: str,
    project_id: str,
    cluster_name: str,
    base_url: str = "https://cloud.mongodb.com/api/atlas/v2",
    max_results: int = 50,
) -> dict[str, Any]:
    """Fetch index suggestions and slow queries from the Atlas Performance Advisor."""
    config = MongoDBAtlasConfig(
        api_public_key=api_public_key,
        api_private_key=api_private_key,
        project_id=project_id,
        base_url=base_url,
        max_results=max_results,
    )
    return get_performance_advisor(config, cluster_name)
