"""Shared MariaDB integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for MariaDB instances.  All operations are production-safe: read-only,
timeouts enforced, result sizes capped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from config.constants.mariadb import (
    MARIADB_DATABASE_ENV,
    MARIADB_HOST_ENV,
    MARIADB_PASSWORD_ENV,
    MARIADB_PORT_ENV,
    MARIADB_SSL_ENV,
    MARIADB_USERNAME_ENV,
)
from config.llm_credentials import resolve_env_credential
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.coercion import safe_int
from infrastructure.text.truncation import truncate
from integrations._relational import RelationalConfigBase, env_bool, env_str
from integrations._validation_helpers import report_classify_failure, report_validation_failure

logger = logging.getLogger(__name__)

DEFAULT_MARIADB_PORT = 3306
DEFAULT_MARIADB_TIMEOUT_SECONDS = 5
DEFAULT_MARIADB_MAX_RESULTS = 50
_QUERY_TRUNCATE_LEN = 200


class MariaDBConfig(RelationalConfigBase):
    """Normalized MariaDB connection settings."""

    host: str = ""
    port: int = DEFAULT_MARIADB_PORT
    database: str = ""
    username: str = ""
    password: str = ""
    ssl: bool = True
    timeout_seconds: int = Field(default=DEFAULT_MARIADB_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_MARIADB_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("password", mode="before")
    @classmethod
    def _normalize_password(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("port", mode="before")
    @classmethod
    def _normalize_port(cls, value: Any) -> int:
        return safe_int(value, DEFAULT_MARIADB_PORT)

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
    host = env_str(MARIADB_HOST_ENV)
    if not host:
        return None
    return build_mariadb_config(
        {
            "host": host,
            "port": env_str(MARIADB_PORT_ENV, str(DEFAULT_MARIADB_PORT)),
            "database": env_str(MARIADB_DATABASE_ENV),
            "username": env_str(MARIADB_USERNAME_ENV),
            "password": resolve_env_credential(MARIADB_PASSWORD_ENV) or "",
            "ssl": env_bool(MARIADB_SSL_ENV, True),
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
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="mariadb",
            method="validate_mariadb_config",
        )
        return MariaDBValidationResult(ok=False, detail=f"MariaDB connection failed: {err}")


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
        return tool_unavailable("mariadb", "Not configured.")

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
                            "query": truncate(row[7] or "", _QUERY_TRUNCATE_LEN),
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
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="mariadb",
            method="get_process_list",
        )
        return tool_unavailable("mariadb", str(err))


def get_global_status(config: MariaDBConfig) -> dict[str, Any]:
    """Retrieve key server metrics from SHOW GLOBAL STATUS.

    Read-only: uses ``SHOW GLOBAL STATUS``.
    Returns a curated subset of important metrics.
    """
    if not config.is_configured:
        return tool_unavailable("mariadb", "Not configured.")

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
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="mariadb",
            method="get_global_status",
        )
        return tool_unavailable("mariadb", str(err))


def get_innodb_status(config: MariaDBConfig) -> dict[str, Any]:
    """Retrieve InnoDB engine status.

    Read-only: uses ``SHOW ENGINE INNODB STATUS``.
    The output text is truncated to prevent excessive result sizes.
    """
    if not config.is_configured:
        return tool_unavailable("mariadb", "Not configured.")

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
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="mariadb",
            method="get_innodb_status",
        )
        return tool_unavailable("mariadb", str(err))


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
        return tool_unavailable("mariadb", "Not configured.")

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
                            "digest_text": truncate(row[0] or "", _QUERY_TRUNCATE_LEN),
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
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="mariadb",
            method="get_slow_queries",
        )
        return tool_unavailable("mariadb", str(err))


def get_replication_status(config: MariaDBConfig) -> dict[str, Any]:
    """Retrieve replication status.

    Read-only: uses ``SHOW ALL SLAVES STATUS`` (MariaDB multi-source
    replication syntax), falling back to ``SHOW SLAVE STATUS`` for
    older versions.
    """
    if not config.is_configured:
        return tool_unavailable("mariadb", "Not configured.")

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
                    except Exception as stmt_err:
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
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="mariadb",
            method="get_replication_status",
        )
        return tool_unavailable("mariadb", str(err))


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[MariaDBConfig | None, str | None]:
    try:
        cfg = build_mariadb_config(
            {
                "host": credentials.get("host", ""),
                "port": credentials.get("port", 3306),
                "database": credentials.get("database", ""),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "ssl": credentials.get("ssl", True),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="mariadb", record_id=record_id)
        return None, None
    if cfg.host and cfg.database:
        return cfg, "mariadb"
    return None, None
