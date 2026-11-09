"""Internal helpers shared by relational integrations."""

from __future__ import annotations

import functools
import logging
import os
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import field_validator

from config.strict_config import StrictConfigModel
from core.tool_framework.utils import tool_unavailable
from integrations._validation_helpers import report_validation_failure

_TRUE_ENV_VALUES = frozenset({"true", "1", "yes"})


def env_bool(name: str, default: bool) -> bool:
    """Return a boolean environment variable with common truthy handling."""
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() in _TRUE_ENV_VALUES


def env_int(name: str, default: int) -> int:
    """Return an integer environment variable, falling back on invalid input."""
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.isdecimal() else default


def env_str(name: str, default: str = "") -> str:
    """Return a stripped environment variable with an optional fallback."""
    normalized = os.getenv(name, default).strip()
    return normalized or default


class RelationalConfigBase(StrictConfigModel):
    """Shared field validators for relational DB config models (host, database, username)."""

    @field_validator("host", mode="before", check_fields=False)
    @classmethod
    def _normalize_host(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("database", mode="before", check_fields=False)
    @classmethod
    def _normalize_database(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("username", mode="before", check_fields=False)
    @classmethod
    def _normalize_username(cls, value: Any) -> str:
        return str(value or "").strip()


def resolve_stored_or_env_config[ConfigT](
    service: str,
    *,
    host: str,
    database: str,
    port: int,
    build_config: Callable[[dict[str, Any] | None], ConfigT],
    env_loader: Callable[[], ConfigT | None],
    extra_from_credentials: Callable[[dict[str, Any]], dict[str, Any]],
    extra_from_env: Callable[[ConfigT], dict[str, Any]],
) -> ConfigT:
    """Resolve a relational config from store first, then env, then identifiers only."""
    from integrations.store import get_integration

    stored = get_integration(service)
    if stored:
        credentials = stored.get("credentials", {})
        if isinstance(credentials, dict):
            return build_config(
                {
                    "host": host,
                    "port": credentials.get("port", port),
                    "database": database,
                    **extra_from_credentials(credentials),
                }
            )

    env_config = env_loader()
    if env_config is not None:
        return build_config(
            {
                "host": host,
                "port": port,
                "database": database,
                **extra_from_env(env_config),
            }
        )

    return build_config({"host": host, "port": port, "database": database})


class SupportsIsConfigured(Protocol):
    """Minimal shape :func:`read_only_query` needs from a relational config."""

    @property
    def is_configured(self) -> bool:
        raise NotImplementedError


# Diagnostic query functions. Deliberately loose: the decorator rewrites the
# signature from ``fn(cursor, config, ...)`` to ``fn(config, ...)``, and spelling
# that precisely needs a ParamSpec, which this codebase does not otherwise use
# and which CodeQL's Python analyzer misreads as an uninitialized local.
type ReadOnlyQuery = Callable[..., dict[str, Any]]


def read_only_query[ConfigT: SupportsIsConfigured](
    *,
    integration: str,
    logger: logging.Logger,
    connect: Callable[[ConfigT], Any],
) -> Callable[[ReadOnlyQuery], ReadOnlyQuery]:
    """Build a decorator that owns the read-only diagnostic-query lifecycle.

    Every relational diagnostic function repeats the same skeleton: bail out
    when the config is incomplete, open a connection, run queries on a cursor,
    close the connection, and convert any failure into the standard
    ``tool_unavailable`` envelope after reporting it. This factory binds the
    vendor-constant pieces (``integration``, ``logger``, ``connect``) once per
    package so each decorated function contains only its own queries and
    result shaping.

    The decorated function is written as ``fn(cursor, config, ...)`` and is
    exposed to callers as ``fn(config, ...)`` — the cursor is supplied by the
    wrapper. ``method`` in the failure report is taken from ``fn.__name__``.

    Cursor semantics stay with the vendor: the wrapper only opens
    ``conn.cursor()`` and closes the connection, so DB-API differences (dict
    vs. tuple rows, driver-specific error types) remain the caller's business.
    """

    def decorator(fn: ReadOnlyQuery, /) -> ReadOnlyQuery:
        @functools.wraps(fn)
        def wrapper(config: ConfigT, /, *args: Any, **kwargs: Any) -> dict[str, Any]:
            if not config.is_configured:
                return tool_unavailable(integration, "Not configured.")
            try:
                conn = connect(config)
                try:
                    with conn.cursor() as cursor:
                        return fn(cursor, config, *args, **kwargs)
                finally:
                    conn.close()
            except Exception as err:
                report_validation_failure(
                    err,
                    logger=logger,
                    integration=integration,
                    method=fn.__name__,
                )
                return tool_unavailable(integration, str(err))

        return wrapper

    return decorator
