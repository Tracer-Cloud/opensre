"""Redis Key Scan Tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.redis import (
    RedisConfig,
    redis_extract_params,
    redis_is_available,
    scan_keys,
)


@tool(
    name="scan_redis_keys",
    description=(
        "Count Redis keys matching a glob pattern and sample their TTL and type. "
        "Uses the non-blocking SCAN cursor (never KEYS) and is safe on large keyspaces."
    ),
    source="redis",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Estimate how many keys match a pattern when investigating key growth or leaks.",
        "Sample TTL and type for matched keys to spot missing expirations or wrong value types.",
    ],
    outputs={
        "matched_keys": "Count of keys matching the pattern (capped at the scan limit).",
        "scan_truncated": "True if the scan hit the iteration cap before completing.",
        "sampled_keys": "Number of keys sampled for TTL/type detail.",
        "samples": "Per-key samples: key, ttl_seconds (-1 none, -2 missing), and type.",
    },
    is_available=redis_is_available,
    injected_params=("host",),
    extract_params=redis_extract_params,
)
def scan_redis_keys(
    host: str,
    port: int = 6379,
    username: str = "",
    password: str = "",
    db: int = 0,
    ssl: bool = False,
    pattern: str = "*",
    sample_limit: int | None = None,
) -> dict[str, Any]:
    """Count and sample Redis keys matching a pattern."""
    config = RedisConfig(host=host, port=port, username=username, password=password, db=db, ssl=ssl)
    return scan_keys(config, pattern=pattern, sample_limit=sample_limit)
