"""Redis Replication Status Tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.redis import (
    RedisConfig,
    get_replication,
    redis_extract_params,
    redis_is_available,
)


@tool(
    name="get_redis_replication",
    description=(
        "Retrieve Redis replication status: node role, master link health, "
        "connected replicas, and per-replica offset lag."
    ),
    source="redis",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Check replication health when investigating stale reads or a failover event.",
        "Measure replica offset lag and master link status across connected replicas.",
    ],
    outputs={
        "role": "Node role: master or slave.",
        "connected_slaves": "Number of replicas connected to a master.",
        "master": "For replicas: master host/port, link status, and sync progress.",
        "replicas": "For masters: per-replica address, state, offset, and lag_bytes.",
    },
    is_available=redis_is_available,
    injected_params=("host",),
    extract_params=redis_extract_params,
)
def get_redis_replication(
    host: str,
    port: int = 6379,
    username: str = "",
    password: str = "",
    db: int = 0,
    ssl: bool = False,
) -> dict[str, Any]:
    """Fetch replication status and replica lag from a Redis instance."""
    config = RedisConfig(host=host, port=port, username=username, password=password, db=db, ssl=ssl)
    return get_replication(config)
