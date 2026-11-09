"""Shared MySQL integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for MySQL instances. All operations are production-safe: read-only,
timeouts enforced, result sizes capped.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from config.constants.mysql import (
    MYSQL_DATABASE_ENV,
    MYSQL_HOST_ENV,
    MYSQL_PASSWORD_ENV,
    MYSQL_PORT_ENV,
    MYSQL_SSL_MODE_ENV,
    MYSQL_USERNAME_ENV,
)
from infrastructure.text.truncation import truncate
from integrations._relational import (
    RelationalConfigBase,
    env_int,
    env_str,
    read_only_query,
    resolve_stored_or_env_config,
)
from integrations._validation_helpers import report_classify_failure, report_validation_failure

logger = logging.getLogger(__name__)

DEFAULT_MYSQL_PORT = 3306
DEFAULT_MYSQL_USER = "root"
DEFAULT_MYSQL_SSL_MODE = "preferred"
DEFAULT_MYSQL_TIMEOUT_SECONDS = 10.0
DEFAULT_MYSQL_MAX_RESULTS = 50

_QUERY_TRUNCATE_LEN = 500


class MySQLConfig(RelationalConfigBase):
    """Normalized MySQL connection settings."""

    host: str = ""
    port: int = DEFAULT_MYSQL_PORT
    database: str = ""
    username: str = DEFAULT_MYSQL_USER
    password: str = ""
    ssl_mode: str = DEFAULT_MYSQL_SSL_MODE  # preferred, required, disabled
    timeout_seconds: float = Field(default=DEFAULT_MYSQL_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_MYSQL_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str:  # type: ignore[override]
        normalized = str(value or DEFAULT_MYSQL_USER).strip()
        return normalized or DEFAULT_MYSQL_USER

    @field_validator("ssl_mode", mode="before")
    @classmethod
    def _normalize_ssl_mode(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_MYSQL_SSL_MODE).strip()
        return normalized or DEFAULT_MYSQL_SSL_MODE

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.database)


@dataclass(frozen=True)
class MySQLValidationResult:
    """Result of validating a MySQL integration."""

    ok: bool
    detail: str


def build_mysql_config(raw: dict[str, Any] | None) -> MySQLConfig:
    """Build a normalized MySQL config object from env/store data."""
    return MySQLConfig.model_validate(raw or {})


def mysql_config_from_env() -> MySQLConfig | None:
    """Load a MySQL config from environment variables."""
    host = env_str(MYSQL_HOST_ENV)
    database = env_str(MYSQL_DATABASE_ENV)
    if not host or not database:
        return None
    return build_mysql_config(
        {
            "host": host,
            "port": env_int(MYSQL_PORT_ENV, DEFAULT_MYSQL_PORT),
            "database": database,
            "username": env_str(MYSQL_USERNAME_ENV, DEFAULT_MYSQL_USER),
            "password": os.getenv(MYSQL_PASSWORD_ENV, ""),
            "ssl_mode": env_str(MYSQL_SSL_MODE_ENV, DEFAULT_MYSQL_SSL_MODE),
        }
    )


def resolve_mysql_config(host: str, database: str, port: int = DEFAULT_MYSQL_PORT) -> MySQLConfig:
    """Build a config for the given host/database, resolving credentials from store or env.

    The LLM supplies only identifying params (host, database, port).
    Credentials (username, password, ssl_mode) are resolved from the stored
    integration or environment variables so they never appear in tool signatures.
    """
    return resolve_stored_or_env_config(
        "mysql",
        host=host,
        database=database,
        port=port,
        build_config=build_mysql_config,
        env_loader=mysql_config_from_env,
        extra_from_credentials=lambda credentials: {
            "username": credentials.get("username", DEFAULT_MYSQL_USER),
            "password": credentials.get("password", ""),
            "ssl_mode": credentials.get("ssl_mode", DEFAULT_MYSQL_SSL_MODE),
        },
        extra_from_env=lambda config: {
            "username": config.username,
            "password": config.password,
            "ssl_mode": config.ssl_mode,
        },
    )


def _build_ssl_context(ssl_mode: str) -> Any:
    """Return an ssl context suitable for pymysql, or None if SSL is disabled."""
    if ssl_mode == "disabled":
        return None
    import ssl as _ssl

    ctx = _ssl.create_default_context()
    if ssl_mode == "preferred":
        # Allow connections to servers without trusted certificates
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
    return ctx


def _get_connection(config: MySQLConfig) -> Any:
    """Create a pymysql connection from config. Caller must close."""
    import pymysql
    import pymysql.cursors

    connect_timeout = max(1, int(config.timeout_seconds))
    ssl_ctx = _build_ssl_context(config.ssl_mode)

    return pymysql.connect(
        host=config.host,
        port=config.port,
        database=config.database,
        user=config.username,
        password=config.password,
        ssl=ssl_ctx,
        connect_timeout=connect_timeout,
        read_timeout=int(config.timeout_seconds),
        write_timeout=int(config.timeout_seconds),
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


# Owns the connect/query/error lifecycle for every read-only diagnostic below.
_read_only = read_only_query(integration="mysql", logger=logger, connect=_get_connection)


def validate_mysql_config(config: MySQLConfig) -> MySQLValidationResult:
    """Validate MySQL connectivity with a lightweight query."""
    if not config.host:
        return MySQLValidationResult(ok=False, detail="MySQL host is required.")
    if not config.database:
        return MySQLValidationResult(ok=False, detail="MySQL database is required.")

    try:
        conn = _get_connection(config)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION()")
                row = cur.fetchone()
                version = row["VERSION()"] if row else "unknown"
                return MySQLValidationResult(
                    ok=True,
                    detail=(f"Connected to MySQL {version}; target database: {config.database}."),
                )
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="mysql",
            method="validate_mysql_config",
        )
        return MySQLValidationResult(ok=False, detail=f"MySQL connection failed: {err}")


def mysql_is_available(sources: dict[str, dict]) -> bool:
    """Check if MySQL integration identifying params are present."""
    my = sources.get("mysql", {})
    return bool(my.get("host") and my.get("database"))


def mysql_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract MySQL identifying params (host, database, port) from resolved integrations.

    Credentials (username, password, ssl_mode) are resolved internally by
    ``resolve_mysql_config`` from the integration store or environment, so
    they never appear in tool signatures and are never seen by the LLM.
    """
    my = sources.get("mysql", {})
    return {
        "host": str(my.get("host", "")).strip(),
        "database": str(my.get("database", "")).strip(),
        "port": int(my.get("port") or DEFAULT_MYSQL_PORT),
    }


@_read_only
def get_server_status(cursor: Any, _config: MySQLConfig) -> dict[str, Any]:
    """Retrieve server status (connections, uptime, InnoDB buffer pool metrics).

    Read-only: uses SHOW GLOBAL STATUS and SHOW VARIABLES.
    """
    _STATUS_KEYS = frozenset(
        {
            "Threads_connected",
            "Threads_running",
            "Threads_created",
            "Connections",
            "Max_used_connections",
            "Slow_queries",
            "Questions",
            "Queries",
            "Aborted_clients",
            "Aborted_connects",
            "Bytes_received",
            "Bytes_sent",
            "Innodb_buffer_pool_reads",
            "Innodb_buffer_pool_read_requests",
            "Innodb_row_lock_waits",
            "Innodb_row_lock_time",
            "Innodb_deadlocks",
            "Uptime",
        }
    )
    _VARIABLE_KEYS = frozenset(
        {
            "max_connections",
            "innodb_buffer_pool_size",
            "version",
            "version_comment",
        }
    )

    cursor.execute("SHOW GLOBAL STATUS")
    all_status = {row["Variable_name"]: row["Value"] for row in cursor.fetchall()}
    metrics = {k: all_status[k] for k in sorted(_STATUS_KEYS) if k in all_status}

    cursor.execute(
        "SHOW VARIABLES WHERE Variable_name IN ("
        + ", ".join(f"'{k}'" for k in sorted(_VARIABLE_KEYS))
        + ")"
    )
    variables = {row["Variable_name"]: row["Value"] for row in cursor.fetchall()}

    # Calculate InnoDB buffer pool hit ratio
    pool_reads = int(all_status.get("Innodb_buffer_pool_reads", 0))
    pool_requests = int(all_status.get("Innodb_buffer_pool_read_requests", 0))
    pool_hit_ratio = 0.0
    if pool_requests > 0:
        pool_hit_ratio = round((1 - pool_reads / pool_requests) * 100, 2)

    return {
        "source": "mysql",
        "available": True,
        "version": variables.get("version", "unknown"),
        "version_comment": variables.get("version_comment", ""),
        "uptime_seconds": int(all_status.get("Uptime", 0)),
        "connections": {
            "current": int(all_status.get("Threads_connected", 0)),
            "running": int(all_status.get("Threads_running", 0)),
            "max": int(variables.get("max_connections", 0)),
            "max_used": int(all_status.get("Max_used_connections", 0)),
            "aborted_clients": int(all_status.get("Aborted_clients", 0)),
            "aborted_connects": int(all_status.get("Aborted_connects", 0)),
        },
        "queries": {
            "total": int(all_status.get("Questions", 0)),
            "slow": int(all_status.get("Slow_queries", 0)),
        },
        "innodb": {
            "buffer_pool_size_bytes": int(variables.get("innodb_buffer_pool_size", 0)),
            "buffer_pool_hit_ratio_percent": pool_hit_ratio,
            "row_lock_waits": int(all_status.get("Innodb_row_lock_waits", 0)),
            "deadlocks": int(all_status.get("Innodb_deadlocks", 0)),
        },
        "metrics": metrics,
    }


@_read_only
def get_current_processes(
    cursor: Any,
    config: MySQLConfig,
    threshold_seconds: int = 1,
) -> dict[str, Any]:
    """Retrieve currently active processes above a duration threshold.

    Read-only: queries information_schema.PROCESSLIST.
    Results are capped at config.max_results.
    """
    cursor.execute(
        """
        SELECT ID, USER, HOST, DB, COMMAND, TIME, STATE, INFO
        FROM information_schema.PROCESSLIST
        WHERE COMMAND != 'Sleep'
          AND ID != CONNECTION_ID()
          AND TIME >= %s
        ORDER BY TIME DESC
        LIMIT %s
        """,
        (threshold_seconds, config.max_results),
    )
    processes = []
    for row in cursor.fetchall():
        processes.append(
            {
                "id": row["ID"],
                "user": row["USER"],
                "host": row["HOST"] or "",
                "database": row["DB"] or "",
                "command": row["COMMAND"],
                "time_seconds": row["TIME"] or 0,
                "state": row["STATE"] or "",
                "query": truncate(row["INFO"] or "", _QUERY_TRUNCATE_LEN),
            }
        )

    return {
        "source": "mysql",
        "available": True,
        "threshold_seconds": threshold_seconds,
        "total_processes": len(processes),
        "processes": processes,
    }


@_read_only
def get_replication_status(cursor: Any, _config: MySQLConfig) -> dict[str, Any]:
    """Retrieve replication status (replica IO/SQL thread health, lag).

    Read-only: uses SHOW REPLICA STATUS (MySQL 8.0.22+) with fallback to
    SHOW SLAVE STATUS for older versions.
    Returns a note if the server is not configured as a replica.
    """
    # Curated fields — includes both old (Slave_*) and new (Replica_*) column names
    _REPLICA_KEYS = (
        "Replica_IO_Running",
        "Replica_SQL_Running",
        "Seconds_Behind_Source",
        "Slave_IO_Running",
        "Slave_SQL_Running",
        "Seconds_Behind_Master",
        "Last_Error",
        "Last_Errno",
        "Source_Host",
        "Master_Host",
        "Source_Port",
        "Master_Port",
        "Retrieved_Gtid_Set",
        "Executed_Gtid_Set",
        "Relay_Log_Space",
        "Exec_Master_Log_Pos",
    )

    rows: list[dict[str, Any]] = []

    # MySQL 8.0.22+ uses SHOW REPLICA STATUS; older uses SHOW SLAVE STATUS
    for stmt in ("SHOW REPLICA STATUS", "SHOW SLAVE STATUS"):
        try:
            cursor.execute(stmt)
            rows = list(cursor.fetchall())
            break
        except Exception as stmt_err:
            import pymysql as _pymysql

            if isinstance(stmt_err, _pymysql.err.ProgrammingError):
                # SHOW REPLICA STATUS not supported on MySQL < 8.0.22; try SHOW SLAVE STATUS fallback
                continue
            raise

    if not rows:
        return {
            "source": "mysql",
            "available": True,
            "note": "This server is not configured as a replica.",
            "replicas": [],
        }

    replicas = []
    for row in rows:
        replicas.append({k: row[k] for k in _REPLICA_KEYS if k in row})

    return {
        "source": "mysql",
        "available": True,
        "replica_count": len(replicas),
        "replicas": replicas,
    }


@_read_only
def get_slow_queries(
    cursor: Any,
    config: MySQLConfig,
    threshold_ms: float = 1000.0,
) -> dict[str, Any]:
    """Retrieve slow query statistics from performance_schema.

    Read-only: queries events_statements_summary_by_digest.
    Results capped at config.max_results.
    Returns an informative note if performance_schema is disabled.
    """
    # performance_schema timer uses picoseconds; convert threshold to picoseconds
    threshold_ps = int(threshold_ms * 1_000_000_000)

    # Check if performance_schema is enabled
    cursor.execute("SELECT @@performance_schema")
    row = cursor.fetchone()
    if not row or not list(row.values())[0]:
        return {
            "source": "mysql",
            "available": True,
            "performance_schema_available": False,
            "note": (
                "performance_schema is disabled. Enable it in my.cnf to collect slow query data."
            ),
            "queries": [],
        }

    cursor.execute(
        """
        SELECT
            DIGEST_TEXT,
            SCHEMA_NAME,
            COUNT_STAR,
            ROUND(AVG_TIMER_WAIT / 1000000000, 3) AS avg_time_ms,
            ROUND(SUM_TIMER_WAIT / 1000000000, 3) AS total_time_ms,
            ROUND(MIN_TIMER_WAIT / 1000000000, 3) AS min_time_ms,
            ROUND(MAX_TIMER_WAIT / 1000000000, 3) AS max_time_ms,
            SUM_ROWS_EXAMINED,
            SUM_ROWS_SENT,
            SUM_NO_INDEX_USED,
            SUM_NO_GOOD_INDEX_USED
        FROM performance_schema.events_statements_summary_by_digest
        WHERE AVG_TIMER_WAIT >= %s
        ORDER BY AVG_TIMER_WAIT DESC
        LIMIT %s
        """,
        (threshold_ps, config.max_results),
    )

    queries = []
    for row in cursor.fetchall():
        queries.append(
            {
                "digest_text": truncate(row["DIGEST_TEXT"] or "", _QUERY_TRUNCATE_LEN),
                "schema_name": row["SCHEMA_NAME"] or "",
                "count": row["COUNT_STAR"] or 0,
                "avg_time_ms": float(row["avg_time_ms"]) if row["avg_time_ms"] is not None else 0.0,
                "total_time_ms": float(row["total_time_ms"])
                if row["total_time_ms"] is not None
                else 0.0,
                "min_time_ms": float(row["min_time_ms"]) if row["min_time_ms"] is not None else 0.0,
                "max_time_ms": float(row["max_time_ms"]) if row["max_time_ms"] is not None else 0.0,
                "rows_examined": row["SUM_ROWS_EXAMINED"] or 0,
                "rows_sent": row["SUM_ROWS_SENT"] or 0,
                "no_index_used": row["SUM_NO_INDEX_USED"] or 0,
                "no_good_index_used": row["SUM_NO_GOOD_INDEX_USED"] or 0,
            }
        )

    return {
        "source": "mysql",
        "available": True,
        "performance_schema_available": True,
        "threshold_ms": threshold_ms,
        "total_queries": len(queries),
        "queries": queries,
    }


@_read_only
def get_table_stats(
    cursor: Any,
    config: MySQLConfig,
) -> dict[str, Any]:
    """Retrieve table statistics (size, row counts) from information_schema.

    Read-only: queries information_schema.TABLES for the configured database.
    Results capped at config.max_results.
    """
    cursor.execute(
        """
        SELECT
            TABLE_NAME,
            ENGINE,
            TABLE_ROWS,
            ROUND(DATA_LENGTH / 1024 / 1024, 3) AS data_mb,
            ROUND(INDEX_LENGTH / 1024 / 1024, 3) AS index_mb,
            ROUND((DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 3) AS total_mb,
            AUTO_INCREMENT,
            TABLE_COLLATION,
            CREATE_TIME,
            UPDATE_TIME
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s
          AND TABLE_TYPE = 'BASE TABLE'
        ORDER BY (DATA_LENGTH + INDEX_LENGTH) DESC
        LIMIT %s
        """,
        (config.database, config.max_results),
    )

    tables = []
    for row in cursor.fetchall():
        tables.append(
            {
                "table_name": row["TABLE_NAME"],
                "engine": row["ENGINE"] or "",
                "row_count_estimate": row["TABLE_ROWS"] or 0,
                "size": {
                    "data_mb": float(row["data_mb"]) if row["data_mb"] is not None else 0.0,
                    "index_mb": float(row["index_mb"]) if row["index_mb"] is not None else 0.0,
                    "total_mb": float(row["total_mb"]) if row["total_mb"] is not None else 0.0,
                },
                "auto_increment": row["AUTO_INCREMENT"],
                "collation": row["TABLE_COLLATION"] or "",
                "created_at": str(row["CREATE_TIME"]) if row["CREATE_TIME"] else None,
                "updated_at": str(row["UPDATE_TIME"]) if row["UPDATE_TIME"] else None,
            }
        )

    return {
        "source": "mysql",
        "available": True,
        "database": config.database,
        "total_tables": len(tables),
        "tables": tables,
    }


def classify(credentials: dict[str, Any], record_id: str) -> tuple[MySQLConfig | None, str | None]:
    try:
        cfg = build_mysql_config(
            {
                "host": credentials.get("host", ""),
                "port": credentials.get("port", 3306),
                "database": credentials.get("database", ""),
                "username": credentials.get("username", "root"),
                "password": credentials.get("password", ""),
                "ssl_mode": credentials.get("ssl_mode", "preferred"),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="mysql", record_id=record_id)
        return None, None
    if cfg.host and cfg.database:
        return cfg, "mysql"
    return None, None
