"""Shared PostgreSQL integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for PostgreSQL instances. All operations are production-safe: read-only,
timeouts enforced, result sizes capped.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from config.constants.postgresql import (
    POSTGRESQL_DATABASE_ENV,
    POSTGRESQL_HOST_ENV,
    POSTGRESQL_PASSWORD_ENV,
    POSTGRESQL_PORT_ENV,
    POSTGRESQL_SSL_MODE_ENV,
    POSTGRESQL_USERNAME_ENV,
)
from core.tool_framework.utils import tool_unavailable
from integrations._relational import (
    RelationalConfigBase,
    env_int,
    env_str,
    resolve_stored_or_env_config,
)
from integrations._validation_helpers import report_classify_failure, report_validation_failure

logger = logging.getLogger(__name__)

DEFAULT_POSTGRESQL_PORT = 5432
DEFAULT_POSTGRESQL_USER = "postgres"
DEFAULT_POSTGRESQL_SSL_MODE = "prefer"
DEFAULT_POSTGRESQL_TIMEOUT_SECONDS = 10.0
DEFAULT_POSTGRESQL_MAX_RESULTS = 50


class PostgreSQLConfig(RelationalConfigBase):
    """Normalized PostgreSQL connection settings."""

    host: str = ""
    port: int = DEFAULT_POSTGRESQL_PORT
    database: str = ""
    username: str = DEFAULT_POSTGRESQL_USER
    password: str = ""
    ssl_mode: str = DEFAULT_POSTGRESQL_SSL_MODE  # prefer, require, disable
    timeout_seconds: float = Field(default=DEFAULT_POSTGRESQL_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_POSTGRESQL_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str:  # type: ignore[override]
        normalized = str(value or DEFAULT_POSTGRESQL_USER).strip()
        return normalized or DEFAULT_POSTGRESQL_USER

    @field_validator("ssl_mode", mode="before")
    @classmethod
    def _normalize_ssl_mode(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_POSTGRESQL_SSL_MODE).strip()
        return normalized or DEFAULT_POSTGRESQL_SSL_MODE

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.database)


@dataclass(frozen=True)
class PostgreSQLValidationResult:
    """Result of validating a PostgreSQL integration."""

    ok: bool
    detail: str


def build_postgresql_config(raw: dict[str, Any] | None) -> PostgreSQLConfig:
    """Build a normalized PostgreSQL config object from env/store data."""
    return PostgreSQLConfig.model_validate(raw or {})


def postgresql_config_from_env() -> PostgreSQLConfig | None:
    """Load a PostgreSQL config from env vars."""
    host = env_str(POSTGRESQL_HOST_ENV)
    database = env_str(POSTGRESQL_DATABASE_ENV)
    if not host or not database:
        return None
    return build_postgresql_config(
        {
            "host": host,
            "port": env_int(POSTGRESQL_PORT_ENV, DEFAULT_POSTGRESQL_PORT),
            "database": database,
            "username": env_str(POSTGRESQL_USERNAME_ENV, DEFAULT_POSTGRESQL_USER),
            "password": os.getenv(POSTGRESQL_PASSWORD_ENV, ""),
            "ssl_mode": env_str(POSTGRESQL_SSL_MODE_ENV, DEFAULT_POSTGRESQL_SSL_MODE),
        }
    )


def resolve_postgresql_config(
    host: str, database: str, port: int = DEFAULT_POSTGRESQL_PORT
) -> PostgreSQLConfig:
    """Build a config for the given host/database, resolving credentials from store or env.

    The LLM supplies only identifying params (host, database, port).
    Credentials (username, password, ssl_mode) are resolved from the stored
    integration or environment variables so they never appear in tool signatures.
    """
    return resolve_stored_or_env_config(
        "postgresql",
        host=host,
        database=database,
        port=port,
        build_config=build_postgresql_config,
        env_loader=postgresql_config_from_env,
        extra_from_credentials=lambda credentials: {
            "username": credentials.get("username", DEFAULT_POSTGRESQL_USER),
            "password": credentials.get("password", ""),
            "ssl_mode": credentials.get("ssl_mode", DEFAULT_POSTGRESQL_SSL_MODE),
        },
        extra_from_env=lambda config: {
            "username": config.username,
            "password": config.password,
            "ssl_mode": config.ssl_mode,
        },
    )


def _get_connection(config: PostgreSQLConfig) -> Any:
    """Create a psycopg2 connection from config. Caller must close."""
    try:
        import psycopg2  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "psycopg2 is not installed. Install it with: pip install psycopg2-binary"
        ) from exc

    return psycopg2.connect(
        host=config.host,
        port=config.port,
        database=config.database,
        user=config.username,
        password=config.password,
        sslmode=config.ssl_mode,
        connect_timeout=int(config.timeout_seconds),
        options=f"-c statement_timeout={int(config.timeout_seconds * 1000)}ms",
        application_name="opensre",
    )


def validate_postgresql_config(config: PostgreSQLConfig) -> PostgreSQLValidationResult:
    """Validate PostgreSQL connectivity with a lightweight query."""
    if not config.host:
        return PostgreSQLValidationResult(ok=False, detail="PostgreSQL host is required.")
    if not config.database:
        return PostgreSQLValidationResult(ok=False, detail="PostgreSQL database is required.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT version()")
            version_info = cursor.fetchone()[0]
            cursor.close()

            # Extract version number from version string
            version = version_info.split()[1] if version_info else "unknown"

            return PostgreSQLValidationResult(
                ok=True,
                detail=(f"Connected to PostgreSQL {version}; target database: {config.database}."),
            )
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="postgresql",
            method="validate_postgresql_config",
        )
        return PostgreSQLValidationResult(ok=False, detail=f"PostgreSQL connection failed: {err}")


def postgresql_is_available(sources: dict[str, dict]) -> bool:
    """Check if PostgreSQL integration identifying params are present."""
    pg = sources.get("postgresql", {})
    return bool(pg.get("host") and pg.get("database"))


def postgresql_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract PostgreSQL identifying params (host, database, port) from resolved integrations.

    Credentials (username, password, ssl_mode) are resolved internally by
    ``resolve_postgresql_config`` from the integration store or environment, so
    they never appear in tool signatures and are never seen by the LLM.
    """
    pg = sources.get("postgresql", {})
    return {
        "host": str(pg.get("host", "")).strip(),
        "database": str(pg.get("database", "")).strip(),
        "port": int(pg.get("port") or DEFAULT_POSTGRESQL_PORT),
    }


def get_server_status(config: PostgreSQLConfig) -> dict[str, Any]:
    """Retrieve server status (connections, databases, uptime, cache hit ratio).

    Read-only: queries system views pg_stat_database and pg_stat_activity.
    """
    if not config.is_configured:
        return tool_unavailable("postgresql", "Not configured.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT version(), date_trunc('second', current_timestamp - pg_postmaster_start_time()) as uptime"
            )
            version_info, uptime = cursor.fetchone()
            version = version_info.split()[1] if version_info else "unknown"

            cursor.execute("""
                SELECT
                    count(*) as total_connections,
                    count(*) FILTER (WHERE state = 'active') as active_connections,
                    count(*) FILTER (WHERE state = 'idle') as idle_connections,
                    max(max_conn.setting::int) as max_connections
                FROM pg_stat_activity, (SELECT setting FROM pg_settings WHERE name = 'max_connections') max_conn
            """)
            conn_stats = cursor.fetchone()

            cursor.execute("""
                SELECT
                    numbackends,
                    xact_commit,
                    xact_rollback,
                    blks_read,
                    blks_hit,
                    tup_returned,
                    tup_fetched,
                    tup_inserted,
                    tup_updated,
                    tup_deleted
                FROM pg_stat_database
                WHERE datname = current_database()
            """)
            db_stats = cursor.fetchone()

            # Calculate cache hit ratio
            cache_hit_ratio = 0.0
            if db_stats and db_stats[3] + db_stats[4] > 0:  # blks_read + blks_hit > 0
                cache_hit_ratio = round((db_stats[4] / (db_stats[3] + db_stats[4])) * 100, 2)

            cursor.close()
            return {
                "source": "postgresql",
                "available": True,
                "version": version,
                "uptime": str(uptime) if uptime else "unknown",
                "connections": {
                    "total": conn_stats[0] if conn_stats else 0,
                    "active": conn_stats[1] if conn_stats else 0,
                    "idle": conn_stats[2] if conn_stats else 0,
                    "max_connections": conn_stats[3] if conn_stats else 0,
                },
                "database_stats": {
                    "backends": db_stats[0] if db_stats else 0,
                    "transactions": {
                        "committed": db_stats[1] if db_stats else 0,
                        "rolled_back": db_stats[2] if db_stats else 0,
                    },
                    "cache_hit_ratio_percent": cache_hit_ratio,
                    "tuples": {
                        "returned": db_stats[5] if db_stats else 0,
                        "fetched": db_stats[6] if db_stats else 0,
                        "inserted": db_stats[7] if db_stats else 0,
                        "updated": db_stats[8] if db_stats else 0,
                        "deleted": db_stats[9] if db_stats else 0,
                    },
                },
            }
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="postgresql",
            method="get_server_status",
        )
        return tool_unavailable("postgresql", str(err))


def get_current_queries(
    config: PostgreSQLConfig,
    threshold_seconds: int = 1,
) -> dict[str, Any]:
    """Retrieve currently running queries above a duration threshold.

    Read-only: queries pg_stat_activity system view.
    Results are capped at config.max_results.
    """
    if not config.is_configured:
        return tool_unavailable("postgresql", "Not configured.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    pid,
                    usename,
                    application_name,
                    client_addr::text,
                    state,
                    query_start,
                    extract(epoch from (now() - query_start))::int as duration_seconds,
                    wait_event_type,
                    wait_event,
                    left(query, 500) as query_truncated
                FROM pg_stat_activity
                WHERE state = 'active'
                    AND query_start IS NOT NULL
                    AND extract(epoch from (now() - query_start)) >= %s
                    AND pid != pg_backend_pid()
                ORDER BY query_start ASC
                LIMIT %s
            """,
                (threshold_seconds, config.max_results),
            )

            queries = []
            for row in cursor.fetchall():
                queries.append(
                    {
                        "pid": row[0],
                        "username": row[1],
                        "application_name": row[2] or "",
                        "client_addr": row[3] or "local",
                        "state": row[4],
                        "query_start": str(row[5]),
                        "duration_seconds": row[6],
                        "wait_event_type": row[7] or "",
                        "wait_event": row[8] or "",
                        "query_truncated": row[9] or "",
                    }
                )

            cursor.close()
            return {
                "source": "postgresql",
                "available": True,
                "threshold_seconds": threshold_seconds,
                "total_queries": len(queries),
                "queries": queries,
            }
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="postgresql",
            method="get_current_queries",
        )
        return tool_unavailable("postgresql", str(err))


def get_replication_status(config: PostgreSQLConfig) -> dict[str, Any]:
    """Retrieve replication status (streaming replicas, WAL positions).

    Read-only: queries pg_stat_replication system view.
    Returns empty replicas list if the server is not a primary or has no replicas.
    """
    if not config.is_configured:
        return tool_unavailable("postgresql", "Not configured.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            # Reliably detect replica status (works on PostgreSQL 10+)
            cursor.execute("SELECT pg_is_in_recovery()")
            is_replica = cursor.fetchone()[0]
            if is_replica:
                cursor.close()
                return {
                    "source": "postgresql",
                    "available": True,
                    "is_primary": False,
                    "replicas": [],
                    "note": "Server is a replica, not a primary.",
                }

            # Primary: check downstream replicas
            cursor.execute("""
                SELECT
                    pid,
                    usename,
                    application_name,
                    client_addr::text,
                    client_hostname,
                    state,
                    sent_lsn,
                    write_lsn,
                    flush_lsn,
                    replay_lsn,
                    write_lag,
                    flush_lag,
                    replay_lag,
                    sync_state
                FROM pg_stat_replication
                ORDER BY application_name, client_addr
            """)

            replicas = []
            for row in cursor.fetchall():
                replicas.append(
                    {
                        "pid": row[0],
                        "username": row[1],
                        "application_name": row[2] or "",
                        "client_addr": row[3] or "local",
                        "client_hostname": row[4] or "",
                        "state": row[5],
                        "sent_lsn": row[6] or "",
                        "write_lsn": row[7] or "",
                        "flush_lsn": row[8] or "",
                        "replay_lsn": row[9] or "",
                        "write_lag": str(row[10]) if row[10] else "",
                        "flush_lag": str(row[11]) if row[11] else "",
                        "replay_lag": str(row[12]) if row[12] else "",
                        "sync_state": row[13] or "",
                    }
                )

            cursor.execute("SELECT pg_current_wal_lsn()")
            current_wal_lsn = cursor.fetchone()[0]

            cursor.close()

            if not replicas:
                return {
                    "source": "postgresql",
                    "available": True,
                    "is_primary": True,
                    "current_wal_lsn": current_wal_lsn,
                    "replicas": [],
                    "note": "Server is a primary but has no active replicas.",
                }

            return {
                "source": "postgresql",
                "available": True,
                "is_primary": True,
                "current_wal_lsn": current_wal_lsn,
                "replica_count": len(replicas),
                "replicas": replicas,
            }
        finally:
            conn.close()
    except Exception as err:
        error_str = str(err)
        if "recovery" in error_str.lower() or "read-only" in error_str.lower():
            return {
                "source": "postgresql",
                "available": True,
                "is_primary": False,
                "replicas": [],
                "note": "Server appears to be a replica, not a primary.",
            }
        report_validation_failure(
            err,
            logger=logger,
            integration="postgresql",
            method="get_replication_status",
        )
        return tool_unavailable("postgresql", error_str)


def get_slow_queries(
    config: PostgreSQLConfig,
    threshold_ms: int = 1000,
    limit: int | None = None,
) -> dict[str, Any]:
    """Retrieve slow query statistics from pg_stat_statements.

    Read-only: queries pg_stat_statements extension view.
    Results capped at config.max_results.
    """
    if not config.is_configured:
        return tool_unavailable("postgresql", "Not configured.")

    effective_limit = min(limit or config.max_results, config.max_results)

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'
            """)

            if not cursor.fetchone():
                cursor.close()
                return {
                    "source": "postgresql",
                    "available": True,
                    "extension_available": False,
                    "note": (
                        "pg_stat_statements extension is not installed. "
                        "Install it with CREATE EXTENSION pg_stat_statements; "
                        "and add 'pg_stat_statements' to shared_preload_libraries."
                    ),
                    "queries": [],
                }

            cursor.execute(
                """
                SELECT
                    queryid,
                    left(query, 500) as query_truncated,
                    calls,
                    round(total_exec_time::numeric, 3) as total_time_ms,
                    round(mean_exec_time::numeric, 3) as mean_time_ms,
                    round(min_exec_time::numeric, 3) as min_time_ms,
                    round(max_exec_time::numeric, 3) as max_time_ms,
                    round(stddev_exec_time::numeric, 3) as stddev_time_ms,
                    rows as total_rows,
                    100.0 * shared_blks_hit / nullif(shared_blks_hit + shared_blks_read, 0) as hit_percent
                FROM pg_stat_statements
                WHERE mean_exec_time >= %s
                ORDER BY mean_exec_time DESC
                LIMIT %s
            """,
                (threshold_ms, effective_limit),
            )

            queries = []
            for row in cursor.fetchall():
                queries.append(
                    {
                        "queryid": str(row[0]) if row[0] else "",
                        "query_truncated": row[1] or "",
                        "calls": row[2],
                        "total_time_ms": float(row[3]),
                        "mean_time_ms": float(row[4]),
                        "min_time_ms": float(row[5]),
                        "max_time_ms": float(row[6]),
                        "stddev_time_ms": float(row[7]),
                        "total_rows": row[8],
                        "cache_hit_percent": round(float(row[9] or 0), 2),
                    }
                )

            cursor.close()
            return {
                "source": "postgresql",
                "available": True,
                "extension_available": True,
                "threshold_ms": threshold_ms,
                "total_queries": len(queries),
                "queries": queries,
            }
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="postgresql",
            method="get_slow_queries",
        )
        return tool_unavailable("postgresql", str(err))


def get_lock_status(config: PostgreSQLConfig) -> dict[str, Any]:
    """Retrieve active locks and blocking relationships.

    Read-only: queries pg_locks and pg_stat_activity system views.
    Results are capped at config.max_results.
    """
    if not config.is_configured:
        return tool_unavailable("postgresql", "Not configured.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    blocked.pid                     AS blocked_pid,
                    blocked.usename                 AS blocked_user,
                    blocked.application_name        AS blocked_app,
                    left(blocked.query, 300)        AS blocked_query,
                    blocking.pid                    AS blocking_pid,
                    blocking.usename                AS blocking_user,
                    blocking.application_name       AS blocking_app,
                    left(blocking.query, 300)       AS blocking_query,
                    extract(epoch from (now() - blocked.query_start))::int AS wait_seconds,
                    blocked_locks.locktype,
                    blocked_locks.relation::regclass::text AS relation
                FROM pg_catalog.pg_locks         blocked_locks
                JOIN pg_catalog.pg_stat_activity blocked
                    ON blocked.pid = blocked_locks.pid
                JOIN pg_catalog.pg_locks         blocking_locks
                    ON blocking_locks.locktype = blocked_locks.locktype
                    AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
                    AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
                    AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
                    AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
                    AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
                    AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
                    AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
                    AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
                    AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
                    AND blocking_locks.pid != blocked_locks.pid
                    AND blocking_locks.granted = true
                JOIN pg_catalog.pg_stat_activity blocking
                    ON blocking.pid = blocking_locks.pid
                WHERE NOT blocked_locks.granted
                ORDER BY wait_seconds DESC
                LIMIT %s
            """,
                (config.max_results,),
            )

            blocked_queries = []
            for row in cursor.fetchall():
                blocked_queries.append(
                    {
                        "blocked_pid": row[0],
                        "blocked_user": row[1] or "",
                        "blocked_app": row[2] or "",
                        "blocked_query": row[3] or "",
                        "blocking_pid": row[4],
                        "blocking_user": row[5] or "",
                        "blocking_app": row[6] or "",
                        "blocking_query": row[7] or "",
                        "wait_seconds": row[8] or 0,
                        "locktype": row[9] or "",
                        "relation": row[10] or "",
                    }
                )

            cursor.execute(
                """
                SELECT
                    locktype,
                    count(*) FILTER (WHERE granted)     AS granted,
                    count(*) FILTER (WHERE NOT granted) AS waiting
                FROM pg_locks
                GROUP BY locktype
                ORDER BY waiting DESC, granted DESC
            """
            )

            lock_summary = []
            for row in cursor.fetchall():
                lock_summary.append(
                    {
                        "locktype": row[0],
                        "granted": row[1],
                        "waiting": row[2],
                    }
                )

            cursor.close()
            return {
                "source": "postgresql",
                "available": True,
                "blocked_query_count": len(blocked_queries),
                "blocked_queries": blocked_queries,
                "lock_summary": lock_summary,
            }
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="postgresql",
            method="get_lock_status",
        )
        return tool_unavailable("postgresql", str(err))


def get_table_stats(
    config: PostgreSQLConfig,
    schema_name: str = "public",
) -> dict[str, Any]:
    """Retrieve table statistics (size, row counts, index usage).

    Read-only: queries pg_stat_user_tables and pg_class system views.
    Results capped at config.max_results.
    """
    if not config.is_configured:
        return tool_unavailable("postgresql", "Not configured.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    schemaname,
                    relname,
                    n_tup_ins,
                    n_tup_upd,
                    n_tup_del,
                    n_live_tup,
                    n_dead_tup,
                    seq_scan,
                    seq_tup_read,
                    idx_scan,
                    idx_tup_fetch,
                    last_vacuum,
                    last_autovacuum,
                    last_analyze,
                    last_autoanalyze,
                    pg_total_relation_size(t.relid) as total_size_bytes,
                    pg_relation_size(t.relid) as table_size_bytes,
                    pg_indexes_size(t.relid) as indexes_size_bytes
                FROM pg_stat_user_tables t
                WHERE schemaname = %s
                ORDER BY pg_total_relation_size(t.relid) DESC
                LIMIT %s
            """,
                (schema_name, config.max_results),
            )

            tables = []
            for row in cursor.fetchall():
                # Calculate index usage ratio
                index_usage = 0.0
                total_scans = (row[7] or 0) + (row[9] or 0)  # seq_scan + idx_scan
                if total_scans > 0:
                    index_usage = round(((row[9] or 0) / total_scans) * 100, 2)

                tables.append(
                    {
                        "schema": row[0],
                        "table_name": row[1],
                        "tuples": {
                            "inserted": row[2] or 0,
                            "updated": row[3] or 0,
                            "deleted": row[4] or 0,
                            "live": row[5] or 0,
                            "dead": row[6] or 0,
                        },
                        "scans": {
                            "sequential": row[7] or 0,
                            "sequential_tuples": row[8] or 0,
                            "index": row[9] or 0,
                            "index_tuples": row[10] or 0,
                            "index_usage_percent": index_usage,
                        },
                        "maintenance": {
                            "last_vacuum": str(row[11]) if row[11] else None,
                            "last_autovacuum": str(row[12]) if row[12] else None,
                            "last_analyze": str(row[13]) if row[13] else None,
                            "last_autoanalyze": str(row[14]) if row[14] else None,
                        },
                        "size": {
                            "total_bytes": row[15] or 0,
                            "table_bytes": row[16] or 0,
                            "indexes_bytes": row[17] or 0,
                            "total_mb": round((row[15] or 0) / 1024 / 1024, 2),
                        },
                    }
                )

            cursor.close()
            return {
                "source": "postgresql",
                "available": True,
                "schema": schema_name,
                "total_tables": len(tables),
                "tables": tables,
            }
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="postgresql",
            method="get_table_stats",
        )
        return tool_unavailable("postgresql", str(err))


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[PostgreSQLConfig | None, str | None]:
    try:
        cfg = build_postgresql_config(
            {
                "host": credentials.get("host", ""),
                "port": credentials.get("port", 5432),
                "database": credentials.get("database", ""),
                "username": credentials.get("username", "postgres"),
                "password": credentials.get("password", ""),
                "ssl_mode": credentials.get("ssl_mode", "prefer"),
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="postgresql", record_id=record_id)
        return None, None
    if cfg.host and cfg.database:
        return cfg, "postgresql"
    return None, None
