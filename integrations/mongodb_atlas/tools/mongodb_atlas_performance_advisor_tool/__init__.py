"""MongoDB Atlas Performance Advisor Tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.mongodb_atlas import (
    MongoDBAtlasConfig,
    atlas_extract_params,
    atlas_is_available,
    get_performance_advisor,
)
from integrations.mongodb_atlas.tools._evidence import (
    map_get_mongodb_atlas_performance_advisor,
)


@tool(
    name="get_mongodb_atlas_performance_advisor",
    description="Retrieve Performance Advisor suggestions for a MongoDB Atlas cluster including recommended indexes and slow query logs.",
    use_cases=[
        "Get index recommendations for a slow Atlas cluster",
        "Review slow query logs surfaced by Atlas Performance Advisor",
    ],
    source="mongodb_atlas",
    surfaces=(ToolSurface.CHAT,),
    is_available=atlas_is_available,
    injected_params=("api_private_key", "api_public_key", "base_url"),
    extract_params=atlas_extract_params,
    evidence_mapper=map_get_mongodb_atlas_performance_advisor,
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
