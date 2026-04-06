"""Shared ClickHouse integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for ClickHouse instances.  All operations are production-safe: read-only,
timeouts enforced, result sizes capped.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

DEFAULT_CLICKHOUSE_PORT = 8123
DEFAULT_CLICKHOUSE_DATABASE = "default"
DEFAULT_CLICKHOUSE_USER = "default"
DEFAULT_CLICKHOUSE_TIMEOUT_SECONDS = 10.0
DEFAULT_CLICKHOUSE_MAX_RESULTS = 50


class ClickHouseConfig(StrictConfigModel):
    """Normalized ClickHouse connection settings."""

    host: str = ""
    port: int = DEFAULT_CLICKHOUSE_PORT
    database: str = DEFAULT_CLICKHOUSE_DATABASE
    username: str = DEFAULT_CLICKHOUSE_USER
    password: str = ""
    secure: bool = False
    timeout_seconds: float = Field(default=DEFAULT_CLICKHOUSE_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_CLICKHOUSE_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("host", mode="before")
    @classmethod
    def _normalize_host(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("database", mode="before")
    @classmethod
    def _normalize_database(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_CLICKHOUSE_DATABASE).strip()
        return normalized or DEFAULT_CLICKHOUSE_DATABASE

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_CLICKHOUSE_USER).strip()
        return normalized or DEFAULT_CLICKHOUSE_USER

    @property
    def is_configured(self) -> bool:
        return bool(self.host)


@dataclass(frozen=True)
class ClickHouseValidationResult:
    """Result of validating a ClickHouse integration."""

    ok: bool
    detail: str


def build_clickhouse_config(raw: dict[str, Any] | None) -> ClickHouseConfig:
    """Build a normalized ClickHouse config object from env/store data."""
    return ClickHouseConfig.model_validate(raw or {})


def clickhouse_config_from_env() -> ClickHouseConfig | None:
    """Load a ClickHouse config from env vars."""
    host = os.getenv("CLICKHOUSE_HOST", "").strip()
    if not host:
        return None
    return build_clickhouse_config(
        {
            "host": host,
            "port": int(os.getenv("CLICKHOUSE_PORT", str(DEFAULT_CLICKHOUSE_PORT)) or str(DEFAULT_CLICKHOUSE_PORT)),
            "database": os.getenv("CLICKHOUSE_DATABASE", DEFAULT_CLICKHOUSE_DATABASE).strip(),
            "username": os.getenv("CLICKHOUSE_USER", DEFAULT_CLICKHOUSE_USER).strip(),
            "password": os.getenv("CLICKHOUSE_PASSWORD", "").strip(),
            "secure": os.getenv("CLICKHOUSE_SECURE", "false").strip().lower()
            in ("true", "1", "yes"),
        }
    )


def _get_client(config: ClickHouseConfig) -> Any:
    """Create a clickhouse_connect Client from config. Caller must close."""
    import clickhouse_connect  # type: ignore[import-not-found]

    return clickhouse_connect.get_client(
        host=config.host,
        port=config.port,
        database=config.database,
        username=config.username,
        password=config.password,
        secure=config.secure,
        connect_timeout=int(config.timeout_seconds),
        send_receive_timeout=int(config.timeout_seconds),
    )


def validate_clickhouse_config(config: ClickHouseConfig) -> ClickHouseValidationResult:
    """Validate ClickHouse connectivity with a lightweight ping query."""
    if not config.host:
        return ClickHouseValidationResult(ok=False, detail="ClickHouse host is required.")

    try:
        client = _get_client(config)
        try:
            result = client.query("SELECT version()")
            version = result.first_row[0] if result.row_count > 0 else "unknown"
            return ClickHouseValidationResult(
                ok=True,
                detail=f"Connected to ClickHouse {version}; database: {config.database}.",
            )
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return ClickHouseValidationResult(ok=False, detail=f"ClickHouse connection failed: {err}")


def get_query_activity(
    config: ClickHouseConfig,
    limit: int | None = None,
) -> dict[str, Any]:
    """Retrieve recent query activity from system.query_log.

    Read-only: queries system.query_log for recent completed queries.
    Results capped at config.max_results.
    """
    if not config.is_configured:
        return {"source": "clickhouse", "available": False, "error": "Not configured."}

    effective_limit = min(limit or config.max_results, config.max_results)
    try:
        client = _get_client(config)
        try:
            result = client.query(
                "SELECT "
                "  query_id, "
                "  type, "
                "  query, "
                "  query_duration_ms, "
                "  read_rows, "
                "  read_bytes, "
                "  result_rows, "
                "  memory_usage, "
                "  event_time "
                "FROM system.query_log "
                "WHERE type = 'QueryFinish' "
                "ORDER BY event_time DESC "
                "LIMIT %(limit)s",
                parameters={"limit": effective_limit},
            )
            queries = []
            for row in result.named_results():
                queries.append(
                    {
                        "query_id": row["query_id"],
                        "type": row["type"],
                        "query": row["query"][:500],
                        "duration_ms": row["query_duration_ms"],
                        "read_rows": row["read_rows"],
                        "read_bytes": row["read_bytes"],
                        "result_rows": row["result_rows"],
                        "memory_usage": row["memory_usage"],
                        "event_time": str(row["event_time"]),
                    }
                )
            return {
                "source": "clickhouse",
                "available": True,
                "total_returned": len(queries),
                "queries": queries,
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "clickhouse", "available": False, "error": str(err)}


def get_system_health(config: ClickHouseConfig) -> dict[str, Any]:
    """Retrieve system health metrics from system.metrics and system.asynchronous_metrics.

    Read-only: queries system tables for server health indicators.
    """
    if not config.is_configured:
        return {"source": "clickhouse", "available": False, "error": "Not configured."}

    try:
        client = _get_client(config)
        try:
            # Get key metrics
            metrics_result = client.query(
                "SELECT metric, value FROM system.metrics "
                "WHERE metric IN ("
                "  'Query', 'Merge', 'PartMutation', "
                "  'ReplicatedFetch', 'ReplicatedSend', "
                "  'TCPConnection', 'HTTPConnection', "
                "  'ReadonlyReplica', 'MaxPartCountForPartition'"
                ")"
            )
            metrics = {}
            for row in metrics_result.named_results():
                metrics[row["metric"]] = row["value"]

            # Get uptime and version
            uptime_result = client.query("SELECT uptime() AS uptime_seconds, version() AS version")
            uptime_row = uptime_result.first_row if uptime_result.row_count > 0 else (0, "unknown")

            return {
                "source": "clickhouse",
                "available": True,
                "version": uptime_row[1],
                "uptime_seconds": uptime_row[0],
                "metrics": metrics,
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "clickhouse", "available": False, "error": str(err)}


def get_table_stats(
    config: ClickHouseConfig,
    database: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Retrieve table size and row count statistics from system.parts.

    Read-only: aggregates system.parts for table-level statistics.
    Results capped at config.max_results.
    """
    if not config.is_configured:
        return {"source": "clickhouse", "available": False, "error": "Not configured."}

    effective_limit = min(limit or config.max_results, config.max_results)
    target_db = database or config.database
    try:
        client = _get_client(config)
        try:
            result = client.query(
                "SELECT "
                "  database, "
                "  table, "
                "  sum(rows) AS total_rows, "
                "  sum(bytes_on_disk) AS total_bytes, "
                "  count() AS part_count, "
                "  max(modification_time) AS last_modified "
                "FROM system.parts "
                "WHERE active = 1 AND database = %(db)s "
                "GROUP BY database, table "
                "ORDER BY total_bytes DESC "
                "LIMIT %(limit)s",
                parameters={"db": target_db, "limit": effective_limit},
            )
            tables = []
            for row in result.named_results():
                tables.append(
                    {
                        "database": row["database"],
                        "table": row["table"],
                        "total_rows": row["total_rows"],
                        "total_bytes": row["total_bytes"],
                        "part_count": row["part_count"],
                        "last_modified": str(row["last_modified"]),
                    }
                )
            return {
                "source": "clickhouse",
                "available": True,
                "database": target_db,
                "total_tables": len(tables),
                "tables": tables,
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "clickhouse", "available": False, "error": str(err)}
