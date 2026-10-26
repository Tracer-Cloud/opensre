"""MySQL Table Stats Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.mysql import (
    get_table_stats,
    mysql_extract_params,
    mysql_is_available,
    resolve_mysql_config,
)


def _map_get_mysql_table_stats(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the table count and the single largest table by total size."""
    if not output.get("available"):
        return
    tables = output.get("tables") or []
    if not tables:
        return
    # Rows are pre-sorted by (DATA_LENGTH + INDEX_LENGTH) DESC, so tables[0] is the largest.
    largest = tables[0]
    largest_mb = (largest.get("size") or {}).get("total_mb", 0)
    record_evidence_entry(
        evidence,
        source="get_mysql_table_stats",
        label="MySQL Table Stats",
        summary=(
            f"{output.get('total_tables', len(tables))} table(s) in '{output.get('database', '')}', "
            f"largest '{largest.get('table_name', '')}' at {largest_mb}MB"
        ),
    )


@tool(
    name="get_mysql_table_stats",
    description="Retrieve MySQL table statistics including row counts and data/index sizes from information_schema.",
    source="mysql",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Identifying the largest tables consuming storage during capacity incidents",
        "Reviewing table sizes and growth patterns for capacity planning",
        "Finding tables with unexpectedly high row counts or index overhead",
    ],
    is_available=mysql_is_available,
    injected_params=("host",),
    extract_params=mysql_extract_params,
    evidence_mapper=_map_get_mysql_table_stats,
)
def get_mysql_table_stats(
    host: str,
    database: str | None = None,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch table statistics for all base tables in the target database."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=resolve_mysql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=get_table_stats,
    )
