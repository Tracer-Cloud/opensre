"""MariaDB InnoDB Status Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.mariadb import (
    MariaDBConfig,
    get_innodb_status,
    mariadb_extract_params,
    mariadb_is_available,
)


def _map_get_mariadb_innodb_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite that InnoDB status was captured, flagging a recorded deadlock section."""
    if not output.get("available"):
        return
    status_text = output.get("innodb_status") or ""
    if not status_text:
        return
    summary = "InnoDB engine status captured"
    if "LATEST DETECTED DEADLOCK" in status_text:
        summary += " — includes a recorded deadlock"
    record_evidence_entry(
        evidence,
        source="get_mariadb_innodb_status",
        label="MariaDB InnoDB Status",
        summary=summary,
    )


@tool(
    name="get_mariadb_innodb_status",
    description="Retrieve InnoDB engine internals including deadlocks, buffer pool state, and I/O activity from SHOW ENGINE INNODB STATUS.",
    use_cases=[
        "Investigate a MariaDB deadlock or lock contention",
        "Inspect InnoDB buffer pool and I/O internals",
    ],
    source="mariadb",
    surfaces=(ToolSurface.CHAT,),
    is_available=mariadb_is_available,
    injected_params=("host", "password", "username"),
    extract_params=mariadb_extract_params,
    evidence_mapper=_map_get_mariadb_innodb_status,
)
def get_mariadb_innodb_status(
    host: str,
    username: str,
    database: str | None = None,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
) -> dict[str, Any]:
    """Fetch InnoDB engine status."""

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
        db_caller=get_innodb_status,
    )
