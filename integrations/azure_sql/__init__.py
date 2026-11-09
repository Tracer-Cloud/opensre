"""Shared Azure SQL integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for Azure SQL Database (managed) instances.  All operations are
production-safe: read-only, timeouts enforced, result sizes capped.

Azure SQL Database is Microsoft's fully managed relational database service
built on SQL Server.  The diagnostic queries leverage Azure-specific DMVs
(Dynamic Management Views) that surface throttling, resource governance,
and service-tier information unavailable in vanilla SQL Server.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from config.constants.azure_sql import (
    AZURE_SQL_DATABASE_ENV,
    AZURE_SQL_DRIVER_ENV,
    AZURE_SQL_ENCRYPT_ENV,
    AZURE_SQL_PASSWORD_ENV,
    AZURE_SQL_PORT_ENV,
    AZURE_SQL_SERVER_ENV,
    AZURE_SQL_USERNAME_ENV,
    DEFAULT_AZURE_SQL_DRIVER,
    DEFAULT_AZURE_SQL_MAX_RESULTS,
    DEFAULT_AZURE_SQL_PORT,
    DEFAULT_AZURE_SQL_TIMEOUT_SECONDS,
)
from config.llm_credentials import resolve_env_credential
from config.strict_config import StrictConfigModel
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.truncation import truncate
from integrations._validation_helpers import report_classify_failure, report_validation_failure

logger = logging.getLogger(__name__)

_QUERY_TRUNCATE_LEN = 500


class AzureSQLConfig(StrictConfigModel):
    """Normalized Azure SQL Database connection settings."""

    server: str = ""
    database: str = ""
    username: str = ""
    password: str = ""
    port: int = DEFAULT_AZURE_SQL_PORT
    driver: str = DEFAULT_AZURE_SQL_DRIVER
    encrypt: bool = True
    timeout_seconds: float = Field(default=DEFAULT_AZURE_SQL_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_AZURE_SQL_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("server", mode="before")
    @classmethod
    def _normalize_server(cls, value: Any) -> str:
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

    @field_validator("driver", mode="before")
    @classmethod
    def _normalize_driver(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_AZURE_SQL_DRIVER).strip()
        return normalized or DEFAULT_AZURE_SQL_DRIVER

    @property
    def is_configured(self) -> bool:
        return bool(self.server and self.database)


@dataclass(frozen=True)
class AzureSQLValidationResult:
    """Result of validating an Azure SQL integration."""

    ok: bool
    detail: str


def build_azure_sql_config(raw: dict[str, Any] | None) -> AzureSQLConfig:
    """Build a normalized Azure SQL config object from env/store data."""
    return AzureSQLConfig.model_validate(raw or {})


def azure_sql_config_from_env() -> AzureSQLConfig | None:
    """Load an Azure SQL config from env vars."""
    server = os.getenv(AZURE_SQL_SERVER_ENV, "").strip()
    database = os.getenv(AZURE_SQL_DATABASE_ENV, "").strip()
    if not server or not database:
        return None
    _port = os.getenv(AZURE_SQL_PORT_ENV, "").strip()
    return build_azure_sql_config(
        {
            "server": server,
            "port": int(_port) if _port.isdigit() else DEFAULT_AZURE_SQL_PORT,
            "database": database,
            "username": os.getenv(AZURE_SQL_USERNAME_ENV, "").strip(),
            "password": resolve_env_credential(AZURE_SQL_PASSWORD_ENV) or "",
            "driver": os.getenv(AZURE_SQL_DRIVER_ENV, DEFAULT_AZURE_SQL_DRIVER).strip(),
            "encrypt": os.getenv(AZURE_SQL_ENCRYPT_ENV, "true").strip().lower()
            in ("true", "1", "yes"),
        }
    )


def resolve_azure_sql_config(
    server: str,
    database: str,
    port: int = DEFAULT_AZURE_SQL_PORT,
) -> AzureSQLConfig:
    """Build a config for the given server/database, resolving credentials from store or env.

    The LLM supplies only identifying params (server, database, port).
    Credentials (username, password, driver, encrypt) are resolved from the stored
    integration or environment variables so they never appear in tool signatures.
    """
    from integrations.store import get_integration

    stored = get_integration("azure_sql")
    if stored:
        creds = stored.get("credentials", {})
        return build_azure_sql_config(
            {
                "server": server,
                "port": creds.get("port", port),
                "database": database,
                "username": creds.get("username", ""),
                "password": creds.get("password", ""),
                "driver": creds.get("driver", DEFAULT_AZURE_SQL_DRIVER),
                "encrypt": creds.get("encrypt", True),
            }
        )

    env_cfg = azure_sql_config_from_env()
    if env_cfg:
        return build_azure_sql_config(
            {
                "server": server,
                "port": port,
                "database": database,
                "username": env_cfg.username,
                "password": env_cfg.password,
                "driver": env_cfg.driver,
                "encrypt": env_cfg.encrypt,
            }
        )

    return build_azure_sql_config({"server": server, "port": port, "database": database})


def _get_connection(config: AzureSQLConfig) -> Any:
    """Create a pyodbc connection from config.  Caller must close."""
    import pyodbc

    encrypt_value = "yes" if config.encrypt else "no"

    def _esc(v: object) -> str:
        return str(v).replace("}", "}}")

    conn_str = (
        f"DRIVER={{{config.driver}}};"
        f"SERVER={config.server},{config.port};"
        f"DATABASE={config.database};"
        f"UID={{{_esc(config.username)}}};"
        f"PWD={{{_esc(config.password)}}};"
        f"Encrypt={encrypt_value};"
        f"TrustServerCertificate=no;"
        f"Connection Timeout={int(config.timeout_seconds)};"
        f"APP=opensre;"
    )
    return pyodbc.connect(conn_str, timeout=int(config.timeout_seconds))


def validate_azure_sql_config(config: AzureSQLConfig) -> AzureSQLValidationResult:
    """Validate Azure SQL connectivity with a lightweight query."""
    if not config.server:
        return AzureSQLValidationResult(ok=False, detail="Azure SQL server is required.")
    if not config.database:
        return AzureSQLValidationResult(ok=False, detail="Azure SQL database is required.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT @@VERSION")
            version_info = cursor.fetchone()[0]
            cursor.close()

            version = version_info.split("\n")[0] if version_info else "unknown"

            return AzureSQLValidationResult(
                ok=True,
                detail=(f"Connected to {version}; target database: {config.database}."),
            )
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="azure_sql",
            method="validate_azure_sql_config",
        )
        return AzureSQLValidationResult(ok=False, detail=f"Azure SQL connection failed: {err}")


def azure_sql_is_available(sources: dict[str, dict]) -> bool:
    """Check if Azure SQL integration identifying params are present."""
    az = sources.get("azure_sql", {})
    return bool(az.get("server") and az.get("database"))


def azure_sql_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract Azure SQL identifying params (server, database, port) from resolved integrations.

    Credentials (username, password, driver, encrypt) are resolved internally by
    ``resolve_azure_sql_config`` from the integration store or environment, so
    they never appear in tool signatures and are never seen by the LLM.
    """
    az = sources.get("azure_sql", {})
    return {
        "server": str(az.get("server") or "").strip(),
        "database": str(az.get("database") or "").strip(),
        "port": int(az.get("port") or DEFAULT_AZURE_SQL_PORT),
    }


# ---------------------------------------------------------------------------
# Read-only diagnostic queries (Azure SQL DMVs)
# ---------------------------------------------------------------------------


def get_server_status(config: AzureSQLConfig) -> dict[str, Any]:
    """Retrieve server status (connections, DTU/vCore usage, service tier).

    Read-only: queries sys.dm_db_resource_stats and sys.database_service_objectives.
    """
    if not config.is_configured:
        return tool_unavailable("azure_sql", "Not configured.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            cursor.execute("SELECT @@VERSION")
            version_info = cursor.fetchone()[0]
            version = version_info.split("\n")[0] if version_info else "unknown"

            cursor.execute("""
                SELECT
                    edition,
                    service_objective,
                    elastic_pool_name
                FROM sys.database_service_objectives
            """)
            slo_row = cursor.fetchone()
            edition = slo_row[0] if slo_row else "unknown"
            service_objective = slo_row[1] if slo_row else "unknown"
            elastic_pool = slo_row[2] if slo_row else None

            cursor.execute("""
                SELECT TOP 1
                    avg_cpu_percent,
                    avg_data_io_percent,
                    avg_log_write_percent,
                    avg_memory_usage_percent,
                    max_worker_percent,
                    max_session_percent,
                    end_time
                FROM sys.dm_db_resource_stats
                ORDER BY end_time DESC
            """)
            resource_row = cursor.fetchone()

            cursor.execute("""
                SELECT
                    COUNT(*) as total_connections,
                    SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as active,
                    SUM(CASE WHEN status = 'sleeping' THEN 1 ELSE 0 END) as idle
                FROM sys.dm_exec_sessions
                WHERE is_user_process = 1
            """)
            conn_row = cursor.fetchone()

            cursor.execute("""
                SELECT
                    SUM(size * 8.0 / 1024) as size_mb
                FROM sys.database_files
            """)
            size_row = cursor.fetchone()

            cursor.close()

            resource_stats: dict[str, Any] = {}
            if resource_row:
                resource_stats = {
                    "avg_cpu_percent": round(float(resource_row[0] or 0), 2),
                    "avg_data_io_percent": round(float(resource_row[1] or 0), 2),
                    "avg_log_write_percent": round(float(resource_row[2] or 0), 2),
                    "avg_memory_usage_percent": round(float(resource_row[3] or 0), 2),
                    "max_worker_percent": round(float(resource_row[4] or 0), 2),
                    "max_session_percent": round(float(resource_row[5] or 0), 2),
                    "sample_time": str(resource_row[6]) if resource_row[6] else None,
                }

            return {
                "source": "azure_sql",
                "available": True,
                "version": version,
                "service_tier": {
                    "edition": edition,
                    "service_objective": service_objective,
                    "elastic_pool": elastic_pool,
                },
                "connections": {
                    "total": conn_row[0] if conn_row else 0,
                    "active": conn_row[1] if conn_row else 0,
                    "idle": conn_row[2] if conn_row else 0,
                },
                "resource_utilization": resource_stats,
                "database_size_mb": round(float(size_row[0] or 0), 2) if size_row else 0,
            }
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="azure_sql",
            method="get_server_status",
        )
        return tool_unavailable("azure_sql", str(err))


def get_current_queries(
    config: AzureSQLConfig,
    threshold_seconds: int = 1,
) -> dict[str, Any]:
    """Retrieve currently running queries above a duration threshold.

    Read-only: queries sys.dm_exec_requests and sys.dm_exec_sql_text.
    Results are capped at config.max_results.
    """
    if not config.is_configured:
        return tool_unavailable("azure_sql", "Not configured.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT TOP (?)
                    r.session_id,
                    s.login_name,
                    s.program_name,
                    s.host_name,
                    r.status,
                    r.start_time,
                    DATEDIFF(SECOND, r.start_time, GETDATE()) as duration_seconds,
                    r.wait_type,
                    r.wait_time,
                    r.cpu_time,
                    r.logical_reads,
                    r.writes,
                    LEFT(t.text, 500) as query_text
                FROM sys.dm_exec_requests r
                JOIN sys.dm_exec_sessions s ON r.session_id = s.session_id
                OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t
                WHERE s.is_user_process = 1
                    AND DATEDIFF(SECOND, r.start_time, GETDATE()) >= ?
                ORDER BY r.start_time ASC
            """,
                (config.max_results, threshold_seconds),
            )

            queries = []
            for row in cursor.fetchall():
                queries.append(
                    {
                        "session_id": row[0],
                        "login_name": row[1] or "",
                        "program_name": row[2] or "",
                        "host_name": row[3] or "",
                        "status": row[4] or "",
                        "start_time": str(row[5]) if row[5] else "",
                        "duration_seconds": row[6] or 0,
                        "wait_type": row[7] or "",
                        "wait_time_ms": row[8] or 0,
                        "cpu_time_ms": row[9] or 0,
                        "logical_reads": row[10] or 0,
                        "writes": row[11] or 0,
                        "query_text": truncate(row[12] or "", _QUERY_TRUNCATE_LEN),
                    }
                )

            cursor.close()
            return {
                "source": "azure_sql",
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
            integration="azure_sql",
            method="get_current_queries",
        )
        return tool_unavailable("azure_sql", str(err))


def get_resource_stats(
    config: AzureSQLConfig,
    minutes: int = 30,
) -> dict[str, Any]:
    """Retrieve resource utilization history from sys.dm_db_resource_stats.

    Azure SQL-specific: exposes DTU/vCore consumption, IO, log throughput,
    and memory pressure over a rolling window.  This is the primary view
    for identifying throttling and tier-limit hits.
    """
    if not config.is_configured:
        return tool_unavailable("azure_sql", "Not configured.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    end_time,
                    avg_cpu_percent,
                    avg_data_io_percent,
                    avg_log_write_percent,
                    avg_memory_usage_percent,
                    max_worker_percent,
                    max_session_percent
                FROM sys.dm_db_resource_stats
                WHERE end_time >= DATEADD(MINUTE, -?, GETDATE())
                ORDER BY end_time DESC
            """,
                (minutes,),
            )

            samples: list[dict[str, Any]] = []
            for row in cursor.fetchall():
                samples.append(
                    {
                        "end_time": str(row[0]) if row[0] else "",
                        "avg_cpu_percent": round(float(row[1] or 0), 2),
                        "avg_data_io_percent": round(float(row[2] or 0), 2),
                        "avg_log_write_percent": round(float(row[3] or 0), 2),
                        "avg_memory_usage_percent": round(float(row[4] or 0), 2),
                        "max_worker_percent": round(float(row[5] or 0), 2),
                        "max_session_percent": round(float(row[6] or 0), 2),
                    }
                )

            cursor.close()

            throttling_risk = "none"
            if samples:
                max_cpu: float = max(float(s["avg_cpu_percent"]) for s in samples)
                max_io: float = max(float(s["avg_data_io_percent"]) for s in samples)
                max_log: float = max(float(s["avg_log_write_percent"]) for s in samples)
                max_workers: float = max(float(s["max_worker_percent"]) for s in samples)
                max_memory: float = max(float(s["avg_memory_usage_percent"]) for s in samples)
                peak: float = max(max_cpu, max_io, max_log, max_workers, max_memory)
                if peak >= 95:
                    throttling_risk = "critical"
                elif peak >= 80:
                    throttling_risk = "high"
                elif peak >= 60:
                    throttling_risk = "moderate"

            return {
                "source": "azure_sql",
                "available": True,
                "window_minutes": minutes,
                "total_samples": len(samples),
                "throttling_risk": throttling_risk,
                "samples": samples,
            }
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="azure_sql",
            method="get_resource_stats",
        )
        return tool_unavailable("azure_sql", str(err))


def get_slow_queries(
    config: AzureSQLConfig,
    threshold_ms: int = 1000,
    limit: int | None = None,
) -> dict[str, Any]:
    """Retrieve slow query statistics from sys.dm_exec_query_stats.

    Read-only: queries the query stats DMV joined with sql_text.
    Results capped at config.max_results, ordered by average elapsed time.
    """
    if not config.is_configured:
        return tool_unavailable("azure_sql", "Not configured.")

    effective_limit = min(limit or config.max_results, config.max_results)

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT TOP (?)
                    qs.query_hash,
                    LEFT(t.text, 500) as query_text,
                    qs.execution_count,
                    qs.total_elapsed_time / 1000.0 as total_time_ms,
                    (qs.total_elapsed_time / qs.execution_count) / 1000.0 as avg_time_ms,
                    qs.min_elapsed_time / 1000.0 as min_time_ms,
                    qs.max_elapsed_time / 1000.0 as max_time_ms,
                    qs.total_logical_reads,
                    qs.total_logical_writes,
                    qs.total_worker_time / 1000.0 as total_cpu_ms
                FROM sys.dm_exec_query_stats qs
                OUTER APPLY sys.dm_exec_sql_text(qs.sql_handle) t
                WHERE (qs.total_elapsed_time / qs.execution_count) / 1000.0 >= ?
                ORDER BY avg_time_ms DESC
            """,
                (effective_limit, threshold_ms),
            )

            queries = []
            for row in cursor.fetchall():
                queries.append(
                    {
                        "query_hash": str(row[0]) if row[0] else "",
                        "query_text": truncate(row[1] or "", _QUERY_TRUNCATE_LEN),
                        "execution_count": row[2] or 0,
                        "total_time_ms": round(float(row[3] or 0), 3),
                        "avg_time_ms": round(float(row[4] or 0), 3),
                        "min_time_ms": round(float(row[5] or 0), 3),
                        "max_time_ms": round(float(row[6] or 0), 3),
                        "total_logical_reads": row[7] or 0,
                        "total_logical_writes": row[8] or 0,
                        "total_cpu_ms": round(float(row[9] or 0), 3),
                    }
                )

            cursor.close()
            return {
                "source": "azure_sql",
                "available": True,
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
            integration="azure_sql",
            method="get_slow_queries",
        )
        return tool_unavailable("azure_sql", str(err))


def get_wait_stats(config: AzureSQLConfig) -> dict[str, Any]:
    """Retrieve top wait statistics from sys.dm_db_wait_stats.

    Azure SQL-specific DMV (not available in on-prem SQL Server).
    Surfaces the most impactful wait types for diagnosing throttling,
    lock contention, IO bottlenecks, and network issues.
    """
    if not config.is_configured:
        return tool_unavailable("azure_sql", "Not configured.")

    try:
        conn = _get_connection(config)
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT TOP (?)
                    wait_type,
                    waiting_tasks_count,
                    wait_time_ms,
                    max_wait_time_ms,
                    signal_wait_time_ms
                FROM sys.dm_db_wait_stats
                WHERE wait_time_ms > 0
                ORDER BY wait_time_ms DESC
            """,
                (config.max_results,),
            )

            waits = []
            for row in cursor.fetchall():
                waits.append(
                    {
                        "wait_type": row[0] or "",
                        "waiting_tasks_count": row[1] or 0,
                        "wait_time_ms": row[2] or 0,
                        "max_wait_time_ms": row[3] or 0,
                        "signal_wait_time_ms": row[4] or 0,
                    }
                )

            cursor.close()
            return {
                "source": "azure_sql",
                "available": True,
                "total_wait_types": len(waits),
                "waits": waits,
            }
        finally:
            conn.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="azure_sql",
            method="get_wait_stats",
        )
        return tool_unavailable("azure_sql", str(err))


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[AzureSQLConfig | None, str | None]:
    try:
        cfg = build_azure_sql_config(
            {
                "server": credentials.get("server", ""),
                "port": credentials.get("port", DEFAULT_AZURE_SQL_PORT),
                "database": credentials.get("database", ""),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "driver": credentials.get("driver", DEFAULT_AZURE_SQL_DRIVER),
                "encrypt": credentials.get("encrypt", True),
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="azure_sql", record_id=record_id)
        return None, None
    if cfg.server and cfg.database:
        return cfg, "azure_sql"
    return None, None
