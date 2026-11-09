"""MySQL Replication Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.mysql import (
    get_replication_status,
    mysql_extract_params,
    mysql_is_available,
    resolve_mysql_config,
)


def _replica_thread_status(replica: dict[str, Any], key_new: str, key_old: str) -> str | None:
    """Read a thread-status field, accepting either the 8.0.22+ or legacy column name."""
    return replica.get(key_new, replica.get(key_old))


def _map_get_mysql_replication_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite replica thread health and lag, or that this server isn't a replica."""
    if not output.get("available"):
        return
    replicas = output.get("replicas") or []
    if not replicas:
        note = output.get("note")
        if note:
            record_evidence_entry(
                evidence,
                source="get_mysql_replication_status",
                label="MySQL Replication Status",
                summary=note,
            )
        return
    stalled = [
        r
        for r in replicas
        if _replica_thread_status(r, "Replica_IO_Running", "Slave_IO_Running") != "Yes"
        or _replica_thread_status(r, "Replica_SQL_Running", "Slave_SQL_Running") != "Yes"
    ]
    lag_values = [
        lag
        for r in replicas
        if (lag := _replica_thread_status(r, "Seconds_Behind_Source", "Seconds_Behind_Master"))
        is not None
    ]
    parts = [f"{len(replicas)} replica(s)"]
    if stalled:
        parts.append(f"{len(stalled)} with a stopped IO/SQL thread")
    if lag_values:
        parts.append(f"max lag {max(lag_values)}s")
    record_evidence_entry(
        evidence,
        source="get_mysql_replication_status",
        label="MySQL Replication Status",
        summary=", ".join(parts),
    )


@tool(
    name="get_mysql_replication_status",
    description="Retrieve MySQL replication status including IO/SQL thread health and replica lag.",
    source="mysql",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Checking replica lag during high-write incidents",
        "Verifying replication IO and SQL threads are running",
        "Diagnosing replication errors and identifying last error details",
    ],
    is_available=mysql_is_available,
    injected_params=("host",),
    extract_params=mysql_extract_params,
    evidence_mapper=_map_get_mysql_replication_status,
)
def get_mysql_replication_status(
    host: str,
    database: str | None = None,
    port: int = 3306,
) -> dict[str, Any]:
    """Fetch replication status from a MySQL instance."""
    return call_db_tool_with_default_db_warning(
        database=database,
        default_db_name="mysql",
        config_resolver=resolve_mysql_config,
        resolver_kwargs={"host": host, "port": port},
        db_caller=get_replication_status,
    )
