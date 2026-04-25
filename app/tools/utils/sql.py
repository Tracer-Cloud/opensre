"""Shared flow logic for SQL-based tools."""

from __future__ import annotations

from typing import Any, Callable


def sql_tool_flow(
    database: str | None,
    default_db: str,
    resolve_func: Callable[..., Any],
    execute_func: Callable[..., dict[str, Any]],
    identifying_params: dict[str, Any],
    execute_params: dict[str, Any],
) -> dict[str, Any]:
    """Execute a standard SQL tool flow: resolve config -> execute query -> handle default DB warning.

    Args:
        database: The database name provided by the user (may be None).
        default_db: The database name to use if 'database' is None.
        resolve_func: Integration helper to resolve config (e.g. resolve_postgresql_config).
        execute_func: Integration helper to execute the query (e.g. get_current_queries).
        identifying_params: Parameters used for resolution (e.g. {'host': host, 'port': port}).
        execute_params: Parameters passed to the execution function (e.g. {'threshold_seconds': 1}).

    Returns:
        The result dictionary from execute_func, potentially with a 'default_db_warning' added.
    """
    _db_defaulted = database is None
    used_database = database if database is not None else default_db

    config = resolve_func(database=used_database, **identifying_params)
    result = execute_func(config, **execute_params)

    if _db_defaulted:
        result["default_db_warning"] = (
            f"WARNING: No database was specified; defaulted to '{default_db}'. "
            "Results may not reflect application data."
        )

    return result
