"""Shared MariaDB integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for MariaDB instances.  All operations are production-safe: read-only,
timeouts enforced, result sizes capped.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

DEFAULT_MARIADB_PORT = 3306
DEFAULT_MARIADB_TIMEOUT_S = 5
DEFAULT_MARIADB_MAX_RESULTS = 50
_QUERY_TRUNCATE_LEN = 200


class MariaDBConfig(StrictConfigModel):
    """Normalized MariaDB connection settings."""

    host: str = ""
    port: int = DEFAULT_MARIADB_PORT
    database: str = ""
    username: str = ""
    password: str = ""
    ssl: bool = True
    timeout_seconds: int = Field(default=DEFAULT_MARIADB_TIMEOUT_S, gt=0)
    max_results: int = Field(default=DEFAULT_MARIADB_MAX_RESULTS, gt=0, le=200)
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
        return str(value or "").strip()

    @field_validator("password", mode="before")
    @classmethod
    def _normalize_password(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("port", mode="before")
    @classmethod
    def _normalize_port(cls, value: Any) -> int:
        try:
            return int(value)
        except (ValueError, TypeError):
            return DEFAULT_MARIADB_PORT

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.database)


@dataclass(frozen=True)
class MariaDBValidationResult:
    """Result of validating a MariaDB integration."""

    ok: bool
    detail: str


def build_mariadb_config(raw: dict[str, Any] | None) -> MariaDBConfig:
    """Build a normalized MariaDB config object from env/store data."""
    return MariaDBConfig.model_validate(raw or {})


def mariadb_config_from_env() -> MariaDBConfig | None:
    """Load a MariaDB config from env vars."""
    host = os.getenv("MARIADB_HOST", "").strip()
    if not host:
        return None
    return build_mariadb_config(
        {
            "host": host,
            "port": os.getenv("MARIADB_PORT", str(DEFAULT_MARIADB_PORT)).strip(),
            "database": os.getenv("MARIADB_DATABASE", "").strip(),
            "username": os.getenv("MARIADB_USERNAME", "").strip(),
            "password": os.getenv("MARIADB_PASSWORD", "").strip(),
            "ssl": os.getenv("MARIADB_SSL", "true").strip().lower() in ("true", "1", "yes"),
        }
    )


def _get_connection(config: MariaDBConfig) -> Any:
    """Create a pymysql connection from config. Caller must close."""
    import ssl as _ssl

    import pymysql

    ssl_ctx: Any = None
    if config.ssl:
        ssl_ctx = _ssl.create_default_context()

    connect_timeout = max(1, int(config.timeout_seconds))

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
    )


def validate_mariadb_config(config: MariaDBConfig) -> MariaDBValidationResult:
    """Validate MariaDB connectivity with a lightweight version query."""
    if not config.host or not config.database:
        return MariaDBValidationResult(ok=False, detail="MariaDB host and database are required.")

    try:
        conn = _get_connection(config)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION()")
                row = cur.fetchone()
                version = row[0] if row else "unknown"
                db_name = config.database or "(default)"
                return MariaDBValidationResult(
                    ok=True,
                    detail=f"Connected to MariaDB {version}; target database: {db_name}.",
                )
        finally:
            conn.close()
    except Exception as err:  # noqa: BLE001
        logger.debug("MariaDB validate_config failed", exc_info=True)
        return MariaDBValidationResult(ok=False, detail=f"MariaDB connection failed: {err}")


def _truncate(text: str, max_len: int = _QUERY_TRUNCATE_LEN) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def mariadb_is_available(sources: dict[str, dict]) -> bool:
    """Check if MariaDB integration credentials are present."""
    mdb = sources.get("mariadb", {})
    return bool(mdb.get("host") and mdb.get("database"))


def mariadb_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract MariaDB credentials from resolved integrations."""
    mdb = sources.get("mariadb", {})
    return {
        "host": mdb.get("host", ""),
        "database": mdb.get("database", ""),
        "username": mdb.get("username", ""),
        "password": mdb.get("password", ""),
        "port": mdb.get("port", DEFAULT_MARIADB_PORT),
        "ssl": mdb.get("ssl", True),
    }


def get_process_list(
    config: MariaDBConfig,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Retrieve active threads from information_schema.PROCESSLIST.

    Read-only: queries the ``information_schema.PROCESSLIST`` table.
    Excludes sleeping connections.  Results capped at ``config.max_results``.
    """
    if not config.is_configured:
        return {"source": "mariadb", "available": False, "error": "Not configured."}

    effective_limit = min(max_results or config.max_results, config.max_results)
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
                    ORDER BY TIME DESC
                    LIMIT %s
                    """,
                    (effective_limit,),
                )
                processes = []
                for row in cur.fetchall():
                    processes.append(
                        {
                            "id": row[0],
                            "user": row[1],
                            "host": row[2],
                            "database": row[3] or "",
                            "command": row[4],
                            "time_secs": row[5] or 0,
                            "state": row[6] or "",
                            "query": _truncate(row[7] or ""),
                        }
                    )
                return {
                    "source": "mariadb",
                    "available": True,
                    "total_processes": len(processes),
                    "processes": processes,
                }
        finally:
            conn.close()
    except Exception as err:  # noqa: BLE001
        logger.debug("MariaDB get_process_list failed", exc_info=True)
        return {"source": "mariadb", "available": False, "error": str(err)}


def get_global_status(config: MariaDBConfig) -> dict[str, Any]:
    """Retrieve key server metrics from SHOW GLOBAL STATUS.

    Read-only: uses ``SHOW GLOBAL STATUS``.
    Returns a curated subset of important metrics.
    """
    if not config.is_configured:
        return {"source": "mariadb", "available": False, "error": "Not configured."}

    _IMPORTANT_KEYS = frozenset(
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

    try:
        conn = _get_connection(config)
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW GLOBAL STATUS")
                all_status = {row[0]: row[1] for row in cur.fetchall()}
                metrics = {k: all_status[k] for k in sorted(_IMPORTANT_KEYS) if k in all_status}
                return {
                    "source": "mariadb",
                    "available": True,
                    "metrics": metrics,
                }
        finally:
            conn.close()
    except Exception as err:  # noqa: BLE001
        logger.debug("MariaDB get_global_status failed", exc_info=True)
        return {"source": "mariadb", "available": False, "error": str(err)}


def get_innodb_status(config: MariaDBConfig) -> dict[str, Any]:
    """Retrieve InnoDB engine status.

    Read-only: uses ``SHOW ENGINE INNODB STATUS``.
    The output text is truncated to prevent excessive result sizes.
    """
    if not config.is_configured:
        return {"source": "mariadb", "available": False, "error": "Not configured."}

    _MAX_STATUS_LEN = 4000

    try:
        conn = _get_connection(config)
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW ENGINE INNODB STATUS")
                row = cur.fetchone()
                status_text = row[2] if row and len(row) > 2 else ""
                if len(status_text) > _MAX_STATUS_LEN:
                    status_text = status_text[:_MAX_STATUS_LEN] + "\n... (truncated)"
                return {
                    "source": "mariadb",
                    "available": True,
                    "innodb_status": status_text,
                }
        finally:
            conn.close()
    except Exception as err:  # noqa: BLE001
        logger.debug("MariaDB get_innodb_status failed", exc_info=True)
        return {"source": "mariadb", "available": False, "error": str(err)}


def get_slow_queries(
    config: MariaDBConfig,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Retrieve slow queries from performance_schema.

    Read-only: queries ``events_statements_summary_by_digest``.
    Returns an informative message if performance_schema is not available.
    Results ordered by average wait time descending.
    """
    if not config.is_configured:
        return {"source": "mariadb", "available": False, "error": "Not configured."}

    effective_limit = min(max_results or config.max_results, config.max_results)
    try:
        conn = _get_connection(config)
        try:
            with conn.cursor() as cur:
                # Check if performance_schema is enabled
                cur.execute("SELECT @@performance_schema")
                row = cur.fetchone()
                if not row or not row[0]:
                    return {
                        "source": "mariadb",
                        "available": True,
                        "note": "performance_schema is disabled. Enable it in my.cnf to collect slow query data.",
                        "queries": [],
                    }

                cur.execute(
                    """
                    SELECT DIGEST_TEXT, COUNT_STAR,
                           ROUND(AVG_TIMER_WAIT / 1000000000, 4) AS avg_time_ms,
                           ROUND(SUM_TIMER_WAIT / 1000000000, 4) AS total_time_ms,
                           SUM_ROWS_EXAMINED, SUM_ROWS_SENT
                    FROM performance_schema.events_statements_summary_by_digest
                    WHERE SCHEMA_NAME = %s
                    ORDER BY AVG_TIMER_WAIT DESC
                    LIMIT %s
                    """,
                    (config.database, effective_limit),
                )
                queries = []
                for row in cur.fetchall():
                    queries.append(
                        {
                            "digest_text": _truncate(row[0] or ""),
                            "count": row[1] or 0,
                            "avg_time_ms": float(row[2]) if row[2] is not None else 0,
                            "total_time_ms": float(row[3]) if row[3] is not None else 0,
                            "rows_examined": row[4] or 0,
                            "rows_sent": row[5] or 0,
                        }
                    )
                return {
                    "source": "mariadb",
                    "available": True,
                    "total_queries": len(queries),
                    "queries": queries,
                }
        finally:
            conn.close()
    except Exception as err:  # noqa: BLE001
        logger.debug("MariaDB get_slow_queries failed", exc_info=True)
        return {"source": "mariadb", "available": False, "error": str(err)}


def get_replication_status(config: MariaDBConfig) -> dict[str, Any]:
    """Retrieve replication status.

    Read-only: uses ``SHOW ALL SLAVES STATUS`` (MariaDB multi-source
    replication syntax), falling back to ``SHOW SLAVE STATUS`` for
    older versions.
    """
    if not config.is_configured:
        return {"source": "mariadb", "available": False, "error": "Not configured."}

    try:
        conn = _get_connection(config)
        try:
            with conn.cursor() as cur:
                rows: list[Any] = []
                columns: list[str] = []
                # MariaDB-specific multi-source syntax first, then legacy
                for stmt in ("SHOW ALL SLAVES STATUS", "SHOW SLAVE STATUS"):
                    try:
                        cur.execute(stmt)
                        rows = list(cur.fetchall())
                        if cur.description:
                            columns = [d[0] for d in cur.description]
                        break
                    except Exception as stmt_err:  # noqa: BLE001
                        import pymysql as _pymysql

                        if isinstance(stmt_err, _pymysql.err.ProgrammingError):
                            continue
                        raise

                if not rows:
                    return {
                        "source": "mariadb",
                        "available": True,
                        "note": "This server is not configured as a replica.",
                        "channels": [],
                    }

                # Return curated subset for each replication channel
                _KEYS = (
                    "Slave_IO_Running",
                    "Slave_SQL_Running",
                    "Seconds_Behind_Master",
                    "Last_Error",
                    "Last_Errno",
                    "Master_Host",
                    "Master_Port",
                    "Master_Log_File",
                    "Relay_Log_Space",
                    "Exec_Master_Log_Pos",
                    "Connection_name",
                )
                channels = []
                for row in rows:
                    full = dict(zip(columns, row))
                    channels.append({k: full[k] for k in _KEYS if k in full})
                return {
                    "source": "mariadb",
                    "available": True,
                    "channels": channels,
                }
        finally:
            conn.close()
    except Exception as err:  # noqa: BLE001
        logger.debug("MariaDB get_replication_status failed", exc_info=True)
        return {"source": "mariadb", "available": False, "error": str(err)}
