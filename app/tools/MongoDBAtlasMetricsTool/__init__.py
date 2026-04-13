"""MongoDB Atlas Cluster Metrics Tool."""

from typing import Any

from app.integrations.mongodb_atlas import (
    MongoDBAtlasConfig,
    atlas_extract_params,
    atlas_is_available,
    get_cluster_metrics,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_mongodb_atlas_cluster_metrics",
    description="Retrieve key process-level metrics for a MongoDB Atlas cluster including connections, opcounters, CPU, memory, cache, and disk IOPS.",
    source="mongodb_atlas",
    surfaces=("investigation", "chat"),
    is_available=atlas_is_available,
    extract_params=atlas_extract_params,
)
def get_mongodb_atlas_cluster_metrics(
    api_public_key: str,
    api_private_key: str,
    project_id: str,
    cluster_name: str,
    base_url: str = "https://cloud.mongodb.com/api/atlas/v2",
    granularity: str = "PT1H",
    period: str = "P1D",
) -> dict[str, Any]:
    """Fetch process-level measurements from the Atlas Admin API."""
    config = MongoDBAtlasConfig(
        api_public_key=api_public_key,
        api_private_key=api_private_key,
        project_id=project_id,
        base_url=base_url,
    )
    return get_cluster_metrics(config, cluster_name, granularity=granularity, period=period)
