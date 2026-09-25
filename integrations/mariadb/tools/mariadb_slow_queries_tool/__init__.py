"""MariaDB Slow Queries Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.mariadb import (
    MariaDBConfig,
    get_slow_queries,
    mariadb_extract_params,
    mariadb_is_available,
)


def _map_get_mariadb_slow_queries(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the slowest query, or why none could be collected."""
    if not output.get("available"):
        return
    queries = output.get("queries") or []
    if not queries:
        note = output.get("note")
        if note:
            record_evidence_entry(
                evidence,
                source="get_mariadb_slow_queries",
                label="MariaDB Slow Queries",
                summary=note,
            )
        return
    # Rows are pre-sorted by AVG_TIMER_WAIT DESC, so queries[0] is the slowest.
    slowest = queries[0]
    record_evidence_entry(
        evidence,
        source="get_mariadb_slow_queries",
        label="MariaDB Slow Queries",
        summary=(
            f"{output.get('total_queries', len(queries))} quer{'y' if len(queries) == 1 else 'ies'} "
            f"surveyed, slowest avg {slowest.get('avg_time_ms', 0)}ms"
        ),
    )


@tool(
    name="get_mariadb_slow_queries",
    description="Retrieve top MariaDB queries by average execution time from performance_schema.events_statements_summary_by_digest.",
    use_cases=[
        "Find the MariaDB statements with the highest average execution time",
        "Pick MariaDB queries to optimize after a latency alert",
    ],
    source="mariadb",
    surfaces=(ToolSurface.CHAT,),
    is_available=mariadb_is_available,
    injected_params=("host", "password", "username"),
    extract_params=mariadb_extract_params,
    evidence_mapper=_map_get_mariadb_slow_queries,
)
def get_mariadb_slow_queries(
    host: str,
    username: str,
    database: str | None = None,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
    max_results: int = 50,
) -> dict[str, Any]:
    """Fetch slow queries from performance_schema."""

    def mariadb_config_builder(database: str) -> MariaDBConfig:
        return MariaDBConfig(
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            ssl=ssl,
            max_results=max_results,
        )

    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=mariadb_config_builder,
        resolver_kwargs={},
        db_caller=get_slow_queries,
    )
