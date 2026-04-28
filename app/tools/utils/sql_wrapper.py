"""Shared SQL tool wrapper helper.

Extracts the repeated ``resolve config → call integration → attach warning``
pattern that all six SQL tools (AzureSQL, PostgreSQL, MySQL, MariaDB) share.

See: https://github.com/Tracer-Cloud/opensre/issues/894
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def run_sql_tool(
    integration_fn: Callable[..., dict[str, Any]],
    /,
    *args: Any,
    warning: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Call *integration_fn* with *args* / *kwargs* and optionally attach a warning.

    This is the shared pattern used across all SQL tools:

    1. Call the integration helper function with the resolved config params
    2. If a *warning* string is provided, inject it into the returned dict
       under the ``"warning"`` key (only when the call succeeds)
    3. Return the dict unchanged on failure

    Parameters
    ----------
    integration_fn:
        The underlying integration function to call
        (e.g. ``query_current_queries``, ``query_slow_queries``).
    *args:
        Positional arguments forwarded to *integration_fn*.
    warning:
        Optional warning message to attach to a successful result.
        Set by tools that need to surface a default-database notice, a
        deprecated-credentials notice, etc.
    **kwargs:
        Keyword arguments forwarded to *integration_fn*.

    Returns
    -------
    dict[str, Any]
        The result dict from *integration_fn*, with ``"warning"`` added
        when *warning* is not ``None`` and the call succeeded.

    Example
    -------
    >>> result = run_sql_tool(
    ...     query_current_queries,
    ...     host=host,
    ...     port=port,
    ...     user=user,
    ...     password=password,
    ...     database=database,
    ...     warning="Using default database — set DB_NAME to target a specific DB.",
    ... )
    """
    result: dict[str, Any] = integration_fn(*args, **kwargs)
    if warning is not None and result.get("available", True):
        result["warning"] = warning
    return result
