"""Redis Client List Tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.redis import (
    RedisConfig,
    get_client_list,
    redis_extract_params,
    redis_is_available,
)


@tool(
    name="get_redis_client_list",
    description=(
        "Summarize connected Redis clients via CLIENT LIST — total connections, "
        "blocked clients (waiting on BLPOP/BRPOP/XREAD), pub/sub clients, and "
        "breakdowns by source address and command — to diagnose connection-pool "
        "exhaustion and stuck or blocked clients."
    ),
    source="redis",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Diagnose connection-pool exhaustion when connected_clients is high or rising.",
        "Find clients blocked on BLPOP/BRPOP/XREAD during a stall or deadlock.",
        "Attribute connection load to a specific source address or command.",
    ],
    outputs={
        "total_clients": "Total currently connected clients.",
        "blocked_clients": "Clients blocked on a blocking command.",
        "pubsub_clients": "Clients in pub/sub mode.",
        "address_breakdown": "Connection count per source address (top N).",
        "command_breakdown": "Connection count per last command (top N).",
        "clients": "Bounded per-client sample: id, addr, idle, flags, db, command.",
    },
    is_available=redis_is_available,
    injected_params=("host",),
    extract_params=redis_extract_params,
)
def get_redis_client_list(
    host: str,
    port: int = 6379,
    username: str = "",
    password: str = "",
    db: int = 0,
    ssl: bool = False,
) -> dict[str, Any]:
    """Summarize connected clients on a Redis instance."""
    config = RedisConfig(host=host, port=port, username=username, password=password, db=db, ssl=ssl)
    return get_client_list(config)
