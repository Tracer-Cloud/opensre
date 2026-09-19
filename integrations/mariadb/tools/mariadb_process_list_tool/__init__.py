"""MariaDB Process List Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import call_db_tool_with_default_db_warning
from integrations.mariadb import (
    MariaDBConfig,
    get_process_list,
    mariadb_extract_params,
    mariadb_is_available,
)


def _map_get_mariadb_process_list(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the returned process count and the longest-running query.

    ``get_process_list`` applies its ``LIMIT`` in SQL itself, so
    ``total_processes`` is always exactly ``len(processes)`` -- there is no
    separate unbounded count. Say "shown" rather than implying this is every
    active process on the server, since a busy server can have more active
    processes than the query's result cap.
    """
    if not output.get("available"):
        return
    processes = output.get("processes") or []
    if not processes:
        return
    longest = max((p.get("time_secs", 0) for p in processes), default=0)
    record_evidence_entry(
        evidence,
        source="get_mariadb_process_list",
        label="MariaDB Process List",
        summary=(
            f"{output.get('total_processes', len(processes))} active process(es) shown, "
            f"longest running {longest}s"
        ),
    )


@tool(
    name="get_mariadb_process_list",
    description=(
        "Retrieve active MariaDB threads and queries from"
        " information_schema.PROCESSLIST, excluding idle connections."
    ),
    use_cases=[
        "Find which MariaDB queries or connections are active right now",
        "Identify a long-running active MariaDB query",
    ],
    source="mariadb",
    surfaces=(ToolSurface.CHAT,),
    is_available=mariadb_is_available,
    injected_params=("host", "password", "username"),
    extract_params=mariadb_extract_params,
    evidence_mapper=_map_get_mariadb_process_list,
)
def get_mariadb_process_list(
    host: str,
    username: str,
    database: str | None = None,
    password: str = "",
    port: int = 3306,
    ssl: bool = True,
    max_results: int = 50,
) -> dict[str, Any]:
    """Fetch active threads from information_schema.PROCESSLIST."""

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
        db_caller=get_process_list,
    )
