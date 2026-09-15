"""MariaDB Replication Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.mariadb import (
    MariaDBConfig,
    get_replication_status,
    mariadb_extract_params,
    mariadb_is_available,
)


def _map_get_mariadb_replication_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite replication channel health and lag, or that this server isn't a replica."""
    if not output.get("available"):
        return
    channels = output.get("channels") or []
    if not channels:
        note = output.get("note")
        if note:
            record_evidence_entry(
                evidence,
                source="get_mariadb_replication_status",
                label="MariaDB Replication Status",
                summary=note,
            )
        return
    stalled = [
        c
        for c in channels
        if c.get("Slave_IO_Running") != "Yes" or c.get("Slave_SQL_Running") != "Yes"
    ]
    lag_values = [
        c["Seconds_Behind_Master"] for c in channels if c.get("Seconds_Behind_Master") is not None
    ]
    parts = [f"{len(channels)} channel(s)"]
    if stalled:
        parts.append(f"{len(stalled)} with a stopped IO/SQL thread")
    if lag_values:
        parts.append(f"max lag {max(lag_values)}s")
    record_evidence_entry(
        evidence,
        source="get_mariadb_replication_status",
        label="MariaDB Replication Status",
        summary=", ".join(parts),
    )


@tool(
    name="get_mariadb_replication_status",
    description="Retrieve MariaDB replication status including I/O and SQL thread state, lag, and errors from SHOW ALL SLAVES STATUS.",
    use_cases=[
        "Check MariaDB replication lag or a stopped replication thread",
        "Diagnose MariaDB replica errors",
    ],
    source="mariadb",
    surfaces=(ToolSurface.CHAT,),
    is_available=mariadb_is_available,
    injected_params=("host", "password", "username"),
    extract_params=mariadb_extract_params,
    evidence_mapper=_map_get_mariadb_replication_status,
)
def get_mariadb_replication_status(
    host: str,
    username: str,
    database: str | None = None,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
) -> dict[str, Any]:
    """Fetch replication status from SHOW ALL SLAVES STATUS."""

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
        db_caller=get_replication_status,
    )
