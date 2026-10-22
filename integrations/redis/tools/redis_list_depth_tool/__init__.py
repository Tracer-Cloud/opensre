"""Redis List/Queue Depth Tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.redis import (
    RedisConfig,
    get_list_depth,
    redis_extract_params,
    redis_is_available,
)


@tool(
    name="get_redis_list_depth",
    description=(
        "Report the depth (LLEN) of a Redis list/queue key, with an optional "
        "bounded head/tail sample (LRANGE), to diagnose queue backlogs and stuck "
        "workers for Sidekiq/Celery/Bull/Resque-style job queues."
    ),
    source="redis",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Check a job-queue backlog when workers fall behind (growing list length).",
        "Inspect the head/tail of a queue to spot stuck, malformed, or poison jobs.",
        "Confirm whether a suspected queue key exists and is actually a list.",
    ],
    outputs={
        "depth": "Number of elements in the list (LLEN); null if the key is not a list.",
        "exists": "Whether the key exists.",
        "type": "The key's Redis type ('list' when valid, 'none' when missing).",
        "head": "Bounded, length-capped sample of the first N elements.",
        "tail": "Bounded, length-capped sample of the last N elements.",
    },
    is_available=redis_is_available,
    injected_params=("host",),
    extract_params=redis_extract_params,
)
def get_redis_list_depth(
    key: str,
    host: str,
    port: int = 6379,
    username: str = "",
    password: str = "",
    db: int = 0,
    ssl: bool = False,
    head: int = 0,
    tail: int = 0,
) -> dict[str, Any]:
    """Report the depth of a Redis list/queue key with an optional head/tail sample."""
    config = RedisConfig(host=host, port=port, username=username, password=password, db=db, ssl=ssl)
    return get_list_depth(config, key=key, head=head, tail=tail)
