"""Shared MySQL integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for MySQL instances. All operations are production-safe: read-only,
timeouts enforced, result sizes capped.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

DEFAULT_MYSQL_PORT = 3306
DEFAULT_MYSQL_USER = "root"
DEFAULT_MYSQL_SSL_MODE = "preferred"
DEFAULT_MYSQL_TIMEOUT_SECONDS = 10.0
DEFAULT_MYSQL_MAX_RESULTS = 50

_QUERY_TRUNCATE_LEN = 500


class MySQLConfig(StrictConfigModel):
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

    @field_validator("host", mode="before")
    @classmethod
    def _normalize_host(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("database", mode="before")
    @classmethod
    def _normalize_database(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str:
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
    host = os.getenv("MYSQL_HOST", "").strip()
    database = os.getenv("MYSQL_DATABASE", "").strip()
    if not host or not database:
        return None
    return build_mysql_config({
        "host": host,
        "port": int(_mysql_port) if (_mysql_port := os.getenv("MYSQL_PORT", "").strip()).isdigit() else DEFAULT_MYSQL_PORT,
        "database": database,
        "username": os.getenv("MYSQL_USERNAME", DEFAULT_MYSQL_USER).strip() or DEFAULT_MYSQL_USER,
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "ssl_mode": os.getenv("MYSQL_SSL_MODE", DEFAULT_MYSQL_SSL_MODE).strip() or DEFAULT_MYSQL_SSL_MODE,
    })


def resolve_mysql_config(host: str, database: str, port: int = DEFAULT_MYSQL_PORT) -> MySQLConfig:
    """Build a config for the given host/database, resolving credentials from store or env.

    The LLM supplies only identifying params (host, database, port).
    Credentials (username, password, ssl_mode) are resolved from the stored
    integration or environment variables so they never appear in tool signatures.
    """
    from app.integrations.store import get_integration

    stored = get_integration("mysql")
    if stored:
        creds = stored.get("credentials", {})
        return build_mysql_config({
            "host": host,
            "port": creds.get("port", port),
            "database": database,
            "username": creds.get("username", DEFAULT_MYSQL_USER),
            "password": creds.get("password", ""),
            "ssl_mode": creds.get("ssl_mode", DEFAULT_MYSQL_SSL_MODE),
        })

    env_cfg = mysql_config_from_env()
    if env_cfg:
        return build_mysql_config({
            "host": host,
            "port": port,
            "database": database,
            "username": env_cfg.username,
            "password": env_cfg.password,
            "ssl_mode": env_cfg.ssl_mode,
        })

    return build_mysql_config({"host": host, "port": port, "database": database})


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
                    detail=(
                        f"Connected to MySQL {version}; "
                        f"target database: {config.database}."
                    ),
                )
        finally:
            conn.close()
    except Exception as err:  # noqa: BLE001
        return MySQLValidationResult(ok=False, detail=f"MySQL connection failed: {err}")


def _truncate(text: str, max_len: int = _QUERY_TRUNCATE_LEN) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def get_server_status(config: MySQLConfig) -> dict[str, Any]:
    """Retrieve server status (connections, uptime, InnoDB buffer pool metrics).

    Read-only: uses SHOW GLOBAL STATUS and SHOW VARIABLES.
    """
    if not config.is_configured:
        return {"source": "mysql", "available": False, "error": "Not configured."}

    _STATUS_KEYS = frozenset({
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
    })
    _VARIABLE_KEYS = frozenset({
        "max_connections",
        "innodb_buffer_pool_size",
        "version",
        "version_comment",
    })

    try:
        conn = _get_connection(config)
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW GLOBAL STATUS")
                all_status = {row["Variable_name"]: row["Value"] for row in cur.fetchall()}
                metrics = {k: all_status[k] for k in sorted(_STATUS_KEYS) if k in all_status}

                cur.execute(
                    "SHOW VARIABLES WHERE Variable_name IN ("
                    + ", ".join(f"'{k}'" for k in sorted(_VARIABLE_KEYS))
                    + ")"
                )
                variables = {row["Variable_name"]: row["Value"] for row in cur.fetchall()}

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
        finally:
            conn.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mysql", "available": False, "error": str(err)}


def get_current_processes(
    config: MySQLConfig,
    threshold_seconds: int = 1,
) -> dict[str, Any]:
    """Retrieve currently active processes above a duration threshold.

    Read-only: queries information_schema.PROCESSLIST.
    Results are capped at config.max_results.
    """
    if not config.is_configured:
        return {"source": "mysql", "available": False, "error": "Not configured."}

    try:
        conn = _get_connection(config)
        try:
            with conn.cursor() as cur:
                cur.execute(
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
                for row in cur.fetchall():
                    processes.append({
                        "id": row["ID"],
                        "user": row["USER"],
                        "host": row["HOST"] or "",
                        "database": row["DB"] or "",
                        "command": row["COMMAND"],
                        "time_seconds": row["TIME"] or 0,
                        "state": row["STATE"] or "",
                        "query": _truncate(row["INFO"] or ""),
                    })

                return {
                    "source": "mysql",
                    "available": True,
                    "threshold_seconds": threshold_seconds,
                    "total_processes": len(processes),
                    "processes": processes,
                }
        finally:
            conn.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mysql", "available": False, "error": str(err)}


def get_replication_status(config: MySQLConfig) -> dict[str, Any]:
    """Retrieve replication status (replica IO/SQL thread health, lag).

    Read-only: uses SHOW REPLICA STATUS (MySQL 8.0.22+) with fallback to
    SHOW SLAVE STATUS for older versions.
    Returns a note if the server is not configured as a replica.
    """
    if not config.is_configured:
        return {"source": "mysql", "available": False, "error": "Not configured."}

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

    try:
        conn = _get_connection(config)
        try:
            with conn.cursor() as cur:
                rows: list[dict[str, Any]] = []

                # MySQL 8.0.22+ uses SHOW REPLICA STATUS; older uses SHOW SLAVE STATUS
                for stmt in ("SHOW REPLICA STATUS", "SHOW SLAVE STATUS"):
                    try:
                        cur.execute(stmt)
                        rows = list(cur.fetchall())
                        break
                    except Exception as stmt_err:  # noqa: BLE001
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
        finally:
            conn.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mysql", "available": False, "error": str(err)}


def get_slow_queries(
    config: MySQLConfig,
    threshold_ms: float = 1000.0,
) -> dict[str, Any]:
    """Retrieve slow query statistics from performance_schema.

    Read-only: queries events_statements_summary_by_digest.
    Results capped at config.max_results.
    Returns an informative note if performance_schema is disabled.
    """
    if not config.is_configured:
        return {"source": "mysql", "available": False, "error": "Not configured."}

    # performance_schema timer uses picoseconds; convert threshold to picoseconds
    threshold_ps = int(threshold_ms * 1_000_000_000)

    try:
        conn = _get_connection(config)
        try:
            with conn.cursor() as cur:
                # Check if performance_schema is enabled
                cur.execute("SELECT @@performance_schema")
                row = cur.fetchone()
                if not row or not list(row.values())[0]:
                    return {
                        "source": "mysql",
                        "available": True,
                        "performance_schema_available": False,
                        "note": (
                            "performance_schema is disabled. "
                            "Enable it in my.cnf to collect slow query data."
                        ),
                        "queries": [],
                    }

                cur.execute(
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
                for row in cur.fetchall():
                    queries.append({
                        "digest_text": _truncate(row["DIGEST_TEXT"] or ""),
                        "schema_name": row["SCHEMA_NAME"] or "",
                        "count": row["COUNT_STAR"] or 0,
                        "avg_time_ms": float(row["avg_time_ms"]) if row["avg_time_ms"] is not None else 0.0,
                        "total_time_ms": float(row["total_time_ms"]) if row["total_time_ms"] is not None else 0.0,
                        "min_time_ms": float(row["min_time_ms"]) if row["min_time_ms"] is not None else 0.0,
                        "max_time_ms": float(row["max_time_ms"]) if row["max_time_ms"] is not None else 0.0,
                        "rows_examined": row["SUM_ROWS_EXAMINED"] or 0,
                        "rows_sent": row["SUM_ROWS_SENT"] or 0,
                        "no_index_used": row["SUM_NO_INDEX_USED"] or 0,
                        "no_good_index_used": row["SUM_NO_GOOD_INDEX_USED"] or 0,
                    })

                return {
                    "source": "mysql",
                    "available": True,
                    "performance_schema_available": True,
                    "threshold_ms": threshold_ms,
                    "total_queries": len(queries),
                    "queries": queries,
                }
        finally:
            conn.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mysql", "available": False, "error": str(err)}


def get_table_stats(
    config: MySQLConfig,
) -> dict[str, Any]:
    """Retrieve table statistics (size, row counts) from information_schema.

    Read-only: queries information_schema.TABLES for the configured database.
    Results capped at config.max_results.
    """
    if not config.is_configured:
        return {"source": "mysql", "available": False, "error": "Not configured."}

    try:
        conn = _get_connection(config)
        try:
            with conn.cursor() as cur:
                cur.execute(
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
                for row in cur.fetchall():
                    tables.append({
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
                    })

                return {
                    "source": "mysql",
                    "available": True,
                    "database": config.database,
                    "total_tables": len(tables),
                    "tables": tables,
                }
        finally:
            conn.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mysql", "available": False, "error": str(err)}
