"""Redis Latency Doctor Tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.redis import (
    RedisConfig,
    get_latency_doctor,
    redis_extract_params,
    redis_is_available,
)


@tool(
    name="get_redis_latency_doctor",
    description=(
        "Run Redis LATENCY DOCTOR to diagnose recent latency spikes (fork/RDB "
        "save, AOF rewrite, blocking commands, slow disk) and list the latest "
        "monitored latency events. Optionally include LATENCY HISTORY for a "
        "specific event."
    ),
    source="redis",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Find the root cause of a Redis latency spike during an incident.",
        "Check whether RDB/AOF persistence or fork is stalling command processing.",
        "Review the history of a specific latency event (e.g. 'command', 'fork').",
    ],
    outputs={
        "report": "Human-readable LATENCY DOCTOR diagnosis.",
        "monitoring_active": "Whether latency monitoring is enabled (latency-monitor-threshold > 0).",
        "monitoring_threshold_ms": "The configured threshold in ms (null if CONFIG GET is denied).",
        "latest": "Latest spike per monitored event: event, last_occurrence, latest_ms, max_ms.",
        "history": "Bounded time series for the requested event (when 'event' is set).",
    },
    is_available=redis_is_available,
    injected_params=("host",),
    extract_params=redis_extract_params,
)
def get_redis_latency_doctor(
    host: str,
    port: int = 6379,
    username: str = "",
    password: str = "",
    db: int = 0,
    ssl: bool = False,
    event: str = "",
    history_limit: int | None = None,
) -> dict[str, Any]:
    """Run LATENCY DOCTOR and report the latest latency events for a Redis instance."""
    config = RedisConfig(host=host, port=port, username=username, password=password, db=db, ssl=ssl)
    return get_latency_doctor(config, event=event, history_limit=history_limit)
