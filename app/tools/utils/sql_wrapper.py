"""Shared wrapper helper for repeated SQL-tool flow pattern.

Centralizes the common pattern of:
  1. Resolving database config (with optional default fallback)
  2. Calling a vendor-specific query/process function
  3. Injecting optional warning when database was defaulted
  4. Returning result dict
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from app.tools.utils.db_warnings import default_db_warning

T = TypeVar("T")


def call_db_tool_with_default_db_warning(  # noqa: UP047
    database: str | None,
    default_db_name: str,
    config_resolver: Callable[..., T],
    resolver_kwargs: dict[str, Any],
    db_caller: Callable[[T], dict[str, Any]],
) -> dict[str, Any]:
    """Wrapper for repeated SQL-tool flow: resolve, call, warn (optional), return.

    Args:
        database: The database parameter from the tool invocation, may be None.
        default_db_name: The default database name if `database` is None (e.g., 'master', 'postgres', 'mysql').
        config_resolver: Function that builds a config object from kwargs (e.g., resolve_azure_sql_config).
        resolver_kwargs: Keyword arguments to pass to config_resolver.
        db_caller: Function that takes the config and returns a dict result.

    Returns:
        The result dict from db_caller, with optional 'default_db_warning' key injected if database was None.
    """
    _db_defaulted = database is None
    if database is None:
        database = default_db_name

    # Resolve config using the vendor-specific resolver
    kwargs = {**resolver_kwargs, "database": database}
    config = config_resolver(**kwargs)

    # Call the vendor-specific query/process function
    result = db_caller(config)

    # Inject warning if database was defaulted
    if _db_defaulted:
        result["default_db_warning"] = default_db_warning(default_db_name)

    return result
