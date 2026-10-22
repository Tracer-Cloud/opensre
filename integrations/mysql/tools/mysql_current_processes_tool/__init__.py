"""MySQL Current Processes Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.mysql import (
    get_current_processes,
    mysql_extract_params,
    mysql_is_available,
    resolve_mysql_config,
)


def _map_get_mysql_current_processes(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the count of long-running processes above the threshold."""
    if not output.get("available"):
        return
    processes = output.get("processes") or []
    if not processes:
        return
    longest = max((p.get("time_seconds", 0) for p in processes), default=0)
    record_evidence_entry(
        evidence,
        source="get_mysql_current_processes",
        label="MySQL Current Processes",
        summary=(
            f"{output.get('total_processes', len(processes))} process(es) over "
            f"{output.get('threshold_seconds', 0)}s, longest running {longest}s"
        ),
    )


@tool(
    name="get_mysql_current_processes",
    description=(
        "Retrieve currently active MySQL processes above a duration threshold,"
        " excluding sleeping connections."
    ),
    source="mysql",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Identifying long-running queries blocking other operations",
        "Investigating lock contention or deadlock situations",
        "Spotting runaway queries during an incident",
    ],
    is_available=mysql_is_available,
    injected_params=("host",),
    extract_params=mysql_extract_params,
    evidence_mapper=_map_get_mysql_current_processes,
)
def get_mysql_current_processes(
    host: str,
    database: str | None = None,
    threshold_seconds: int = 1,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch active processes running longer than threshold_seconds (default 1s)."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=resolve_mysql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=lambda config: get_current_processes(config, threshold_seconds=threshold_seconds),
    )
