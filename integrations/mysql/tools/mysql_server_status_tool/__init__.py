"""MySQL Server Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.mysql import (
    get_server_status,
    mysql_extract_params,
    mysql_is_available,
    resolve_mysql_config,
)


def _map_get_mysql_server_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite version, connection load, and InnoDB deadlock count."""
    if not output.get("available"):
        return
    connections = output.get("connections") or {}
    innodb = output.get("innodb") or {}
    parts = [
        f"MySQL {output.get('version', 'unknown')}",
        f"{connections.get('current', 0)}/{connections.get('max', 0)} connections",
    ]
    deadlocks = innodb.get("deadlocks", 0)
    if deadlocks:
        parts.append(f"{deadlocks} InnoDB deadlock(s)")
    record_evidence_entry(
        evidence,
        source="get_mysql_server_status",
        label="MySQL Server Status",
        summary=", ".join(parts),
    )


@tool(
    name="get_mysql_server_status",
    description="Retrieve MySQL server metrics including connections, uptime, query rates, and InnoDB buffer pool statistics.",
    source="mysql",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Checking MySQL server health during an incident",
        "Identifying connection saturation or exhaustion issues",
        "Reviewing InnoDB buffer pool hit ratio and deadlock counts",
    ],
    is_available=mysql_is_available,
    injected_params=("host",),
    extract_params=mysql_extract_params,
    evidence_mapper=_map_get_mysql_server_status,
)
def get_mysql_server_status(
    host: str,
    database: str | None = None,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch server status metrics from a MySQL instance."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=resolve_mysql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=get_server_status,
    )
