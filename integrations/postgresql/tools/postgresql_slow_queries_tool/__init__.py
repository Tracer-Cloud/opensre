"""PostgreSQL Slow Queries Tool."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from core.domain.types.tools import ToolSurface
from core.tool import EvidenceType, SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.postgresql import (
    get_slow_queries,
    postgresql_extract_params,
    postgresql_is_available,
    resolve_postgresql_config,
)


class PostgreSQLSlowQueriesInput(BaseModel):
    host: str = Field(description="PostgreSQL host or endpoint name.")
    database: str | None = Field(
        default=None,
        description="Target database name. Defaults to integration database when omitted.",
    )
    threshold_ms: int = Field(
        default=1000,
        description="Minimum mean execution time (ms) for query inclusion.",
    )
    port: int = Field(default=5432, description="PostgreSQL TCP port.")


class PostgreSQLSlowQueriesOutput(BaseModel):
    source: str = Field(description="Evidence source label.")
    available: bool = Field(description="Whether query stats were retrieved.")
    queries: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Slow query rows ranked by mean execution time.",
    )
    total_queries: int = Field(default=0, description="Number of slow query rows returned.")
    threshold_ms: int | None = Field(default=None, description="Applied threshold in ms.")
    database: str | None = Field(default=None, description="Database queried for stats.")
    default_db_warning: str | None = Field(
        default=None,
        description="Warning emitted when the default database fallback is used.",
    )
    error: str | None = Field(default=None, description="Error details when query fails.")


@tool(
    name="get_postgresql_slow_queries",
    description=(
        "Retrieve slow PostgreSQL queries from pg_stat_statements extension, ranked"
        " by mean execution time."
    ),
    source="postgresql",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Identifying slow queries that may be causing performance degradation",
        "Analyzing query execution patterns during incident timeframes",
        "Finding poorly optimized queries with high execution times or low cache hit rates",
    ],
    source_id="postgresql_pg_stat_statements",
    evidence_type=EvidenceType.QUERY_STATS,
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    examples=[
        "List slow queries above 1000ms to diagnose database latency spikes.",
        "Lower threshold to 200ms to inspect emerging query regressions.",
    ],
    anti_examples=["Use this tool for pod restart loops or Kubernetes health checks."],
    input_model=PostgreSQLSlowQueriesInput,
    output_model=PostgreSQLSlowQueriesOutput,
    is_available=postgresql_is_available,
    injected_params=("host",),
    extract_params=postgresql_extract_params,
)
def get_postgresql_slow_queries(
    host: str,
    database: str | None = None,
    threshold_ms: int = 1000,
    port: int = 5432,
) -> dict[str, Any]:
    """Fetch slow query statistics above the threshold (default 1000ms mean time)."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="postgres",
        config_resolver=resolve_postgresql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=lambda config: get_slow_queries(config, threshold_ms=threshold_ms),
    )
