"""MariaDB Global Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.mariadb import (
    MariaDBConfig,
    get_global_status,
    mariadb_extract_params,
    mariadb_is_available,
)


def _map_get_mariadb_global_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite uptime, connected threads, and InnoDB deadlock count."""
    if not output.get("available"):
        return
    metrics = output.get("metrics") or {}
    if not metrics:
        return
    parts = []
    if (uptime := metrics.get("Uptime")) is not None:
        parts.append(f"uptime {uptime}s")
    if (connected := metrics.get("Threads_connected")) is not None:
        parts.append(f"{connected} thread(s) connected")
    deadlocks = int(metrics.get("Innodb_deadlocks") or 0)
    if deadlocks:
        parts.append(f"{deadlocks} InnoDB deadlock(s)")
    if not parts:
        return
    record_evidence_entry(
        evidence,
        source="get_mariadb_global_status",
        label="MariaDB Global Status",
        summary=", ".join(parts),
    )


@tool(
    name="get_mariadb_global_status",
    description="Retrieve key MariaDB server metrics including connections, threads, slow queries, InnoDB buffer pool stats, and uptime from SHOW GLOBAL STATUS.",
    use_cases=[
        "Check MariaDB connection counts, thread usage, and buffer pool health",
        "Check server-wide connection pressure and the cumulative slow-query count",
    ],
    source="mariadb",
    surfaces=(ToolSurface.CHAT,),
    is_available=mariadb_is_available,
    injected_params=("host", "password", "username"),
    extract_params=mariadb_extract_params,
    evidence_mapper=_map_get_mariadb_global_status,
)
def get_mariadb_global_status(
    host: str,
    username: str,
    database: str | None = None,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
) -> dict[str, Any]:
    """Fetch curated server metrics from SHOW GLOBAL STATUS."""

    def mariadb_config_builder(database: str) -> MariaDBConfig:
        return MariaDBConfig(
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            ssl=ssl,
        )

    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=mariadb_config_builder,
        resolver_kwargs={},
        db_caller=get_global_status,
    )
