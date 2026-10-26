"""Redis Slow Log Tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.redis import (
    RedisConfig,
    get_slowlog,
    redis_extract_params,
    redis_is_available,
)


@tool(
    name="get_redis_slowlog",
    description=(
        "Retrieve recent Redis slow log entries, including the command, "
        "execution duration, and originating client, to surface slow commands."
    ),
    source="redis",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Identify slow Redis commands when latency or timeouts are reported.",
        "Correlate a latency spike with specific expensive commands and their callers.",
    ],
    outputs={
        "returned_entries": "Number of slow log entries returned (capped at max_results).",
        "entries": "Slow log records: id, start_time, duration_microseconds, command, and client.",
    },
    is_available=redis_is_available,
    injected_params=("host",),
    extract_params=redis_extract_params,
)
def get_redis_slowlog(
    host: str,
    port: int = 6379,
    username: str = "",
    password: str = "",
    db: int = 0,
    ssl: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Fetch recent slow log entries from a Redis instance."""
    config = RedisConfig(host=host, port=port, username=username, password=password, db=db, ssl=ssl)
    return get_slowlog(config, limit=limit)
