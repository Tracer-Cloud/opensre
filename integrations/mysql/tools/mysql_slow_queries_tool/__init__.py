"""MySQL Slow Queries Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.mysql import (
    get_slow_queries,
    mysql_extract_params,
    mysql_is_available,
    resolve_mysql_config,
)


def _map_get_mysql_slow_queries(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the slow-query count and worst offender, or why none could be collected."""
    if not output.get("available"):
        return
    if not output.get("performance_schema_available", True):
        record_evidence_entry(
            evidence,
            source="get_mysql_slow_queries",
            label="MySQL Slow Queries",
            summary=output.get("note", "performance_schema is disabled."),
        )
        return
    queries = output.get("queries") or []
    if not queries:
        return
    # Rows are pre-sorted by AVG_TIMER_WAIT DESC, so queries[0] is the slowest.
    slowest = queries[0]
    record_evidence_entry(
        evidence,
        source="get_mysql_slow_queries",
        label="MySQL Slow Queries",
        summary=(
            f"{output.get('total_queries', len(queries))} quer{'y' if len(queries) == 1 else 'ies'} "
            f"above {output.get('threshold_ms', 0)}ms, slowest avg {slowest.get('avg_time_ms', 0)}ms"
        ),
    )


@tool(
    name="get_mysql_slow_queries",
    description="Retrieve slow MySQL queries from performance_schema, ranked by average execution time.",
    source="mysql",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Identifying slow queries that may be causing performance degradation",
        "Analyzing query execution patterns during incident timeframes",
        "Finding poorly optimized queries with high execution times or full-table scans",
    ],
    is_available=mysql_is_available,
    injected_params=("host",),
    extract_params=mysql_extract_params,
    evidence_mapper=_map_get_mysql_slow_queries,
)
def get_mysql_slow_queries(
    host: str,
    database: str | None = None,
    threshold_ms: float = 1000.0,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch slow query statistics above threshold_ms mean execution time (default 1000ms)."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=resolve_mysql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=lambda config: get_slow_queries(config, threshold_ms=threshold_ms),
    )
