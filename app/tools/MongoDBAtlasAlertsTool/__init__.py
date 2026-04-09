"""MongoDB Atlas Alerts Tool."""

from typing import Any

from app.integrations.mongodb_atlas import (
    MongoDBAtlasConfig,
    atlas_extract_params,
    atlas_is_available,
    get_alerts,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mongodb_atlas_alerts",
    description="Retrieve open alerts for a MongoDB Atlas project including event type, metric, cluster, and current value.",
    source="mongodb_atlas",
    surfaces=("investigation", "chat"),
    is_available=atlas_is_available,
    extract_params=atlas_extract_params,
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
