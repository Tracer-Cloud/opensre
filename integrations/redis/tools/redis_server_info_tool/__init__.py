"""Redis Server Info Tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.redis import (
    RedisConfig,
    get_server_info,
    redis_extract_params,
    redis_is_available,
)


@tool(
    name="get_redis_server_info",
    description=(
        "Retrieve Redis server info including memory usage, connected clients, "
        "keyspace statistics, and hit/miss and eviction counters."
    ),
    source="redis",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Assess Redis health during an incident: memory pressure, eviction, and client load.",
        "Check used vs. max memory and the maxmemory-policy when investigating OOM or latency.",
        "Read keyspace hit/miss ratios to spot cache-effectiveness regressions.",
    ],
    outputs={
        "memory": "Used, peak, and RSS bytes, maxmemory, fragmentation ratio, and eviction policy.",
        "clients": "Connected, blocked, and tracking client counts.",
        "stats": "Connection/command counters, ops/sec, keyspace hits/misses, evicted/expired keys.",
        "keyspace": "Per-database key counts, expires, and average TTL.",
    },
    is_available=redis_is_available,
    injected_params=("host",),
    extract_params=redis_extract_params,
)
def get_redis_server_info(
    host: str,
    port: int = 6379,
    username: str = "",
    password: str = "",
    db: int = 0,
    ssl: bool = False,
) -> dict[str, Any]:
    """Fetch server info metrics from a Redis instance."""
    config = RedisConfig(host=host, port=port, username=username, password=password, db=db, ssl=ssl)
    return get_server_info(config)
