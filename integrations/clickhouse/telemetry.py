"""Read the pinned ClickStack legacy Map-schema through bounded ClickHouse SDK streams."""

from __future__ import annotations

import json
from typing import Any, Literal

import clickhouse_connect  # type: ignore[import-untyped]
from clickhouse_connect.driver import Client  # type: ignore[import-untyped]
from pydantic import Field
from urllib3 import PoolManager

from config.constants.telemetry import (
    TELEMETRY_CLICKHOUSE_TIMEOUT_SECONDS,
    TELEMETRY_QUERY_MAX_RESPONSE_BYTES,
    TELEMETRY_QUERY_MAX_RESULT_ROWS,
)
from config.strict_config import StrictConfigModel
from core.telemetry.models import Evidence, Measurement, MetricMapping, Scope


class ClickHouseEvidenceConfig(StrictConfigModel):
    backend: Literal["clickhouse"] = "clickhouse"
    host: str = Field(default="127.0.0.1", pattern=r"^[a-zA-Z0-9.:-]+$")
    port: int = Field(default=18123, ge=1, le=65535)
    database: str = Field(default="default", pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    username: str = Field(default="opensre_reader", min_length=1, max_length=200)
    secure: bool = False


class ClickHouseEvidenceReader:
    def __init__(
        self, config: ClickHouseEvidenceConfig, password: str, *, client: Client | None = None
    ) -> None:
        self.config = config
        self._password = password
        self._client = client
        self._owns_client = client is None
        self._pool: PoolManager | None = None

    def close(self) -> None:
        """Release only an internally owned SDK client and connection pool."""
        if not self._owns_client:
            return
        try:
            if self._client is not None:
                self._client.close()
        finally:
            if self._pool is not None:
                self._pool.clear()

    def _get_client(self) -> Client:
        if self._client is None:
            # Ignore ambient proxy variables and isolate this reader's connection cleanup.
            pool = PoolManager()
            host = self.config.host
            if ":" in host:
                host = f"[{host}]"
            try:
                self._client = clickhouse_connect.get_client(
                    host=host,
                    port=self.config.port,
                    database=self.config.database,
                    username=self.config.username,
                    password=self._password,
                    secure=self.config.secure,
                    interface="https" if self.config.secure else "http",
                    connect_timeout=TELEMETRY_CLICKHOUSE_TIMEOUT_SECONDS,
                    send_receive_timeout=TELEMETRY_CLICKHOUSE_TIMEOUT_SECONDS,
                    pool_mgr=pool,
                    compress=False,
                    autogenerate_session_id=False,
                    query_retries=0,
                )
            except Exception:
                pool.clear()
                raise
            self._pool = pool
        return self._client

    def verify(self) -> None:
        """Require database-enforced read-only credentials, not just a request setting."""
        rows = self._query("SELECT getSetting('readonly') AS readonly", {}, readonly=False)
        if not rows or int(rows[0]["readonly"]) not in {1, 2}:
            raise ValueError(
                "Use a dedicated read-only ClickHouse user, not collector/admin credentials."
            )

    def _query(
        self, sql: str, parameters: dict[str, object], *, readonly: bool = True
    ) -> list[dict[str, Any]]:
        settings = {
            "max_execution_time": "5",
            "max_rows_to_read": "500000",
            "max_bytes_to_read": "50000000",
            "max_result_rows": str(TELEMETRY_QUERY_MAX_RESULT_ROWS),
            "result_overflow_mode": "throw",
            "output_format_json_quote_64bit_integers": "0",
        }
        if readonly:
            settings["readonly"] = "2"
        # The SDK owns transport and parameter binding; bound bytes before JSON decoding.
        with self._get_client().raw_stream(
            sql,
            parameters=parameters,
            settings=settings,
            fmt="JSON",
        ) as response:
            payload = response.read(TELEMETRY_QUERY_MAX_RESPONSE_BYTES + 1)
        if len(payload) > TELEMETRY_QUERY_MAX_RESPONSE_BYTES:
            raise ValueError("Telemetry response exceeds the byte limit.")
        data = json.loads(payload)["data"]
        if not isinstance(data, list) or len(data) > TELEMETRY_QUERY_MAX_RESULT_ROWS:
            raise ValueError("Telemetry response exceeds the row limit.")
        return data

    @staticmethod
    def _parameters(scope: Scope) -> dict[str, object]:
        return {
            "service": scope.service,
            "environment": scope.environment,
            "start": scope.start,
            "end": scope.end,
        }

    def measure(self, scope: Scope, mapping: MetricMapping) -> Measurement:
        """Aggregate only delta SERVER-span calls, never sampled log counts."""
        rows = self._query(
            "SELECT sum(Value) AS total, "
            "sumIf(Value, Attributes[{status:String}] = {error:String}) AS errors, "
            "max(toUnixTimestamp64Milli(TimeUnix)) / 1000 AS latest, "
            "countIf(AggregationTemporality != 1 OR IsMonotonic != 1 OR Value < 0) AS invalid "
            "FROM otel_metrics_sum WHERE MetricName = {metric:String} "
            "AND if(ServiceName != '', ServiceName, Attributes['service.name']) = {service:String} "
            "AND if(ResourceAttributes['deployment.environment.name'] != '', "
            "ResourceAttributes['deployment.environment.name'], "
            "Attributes['deployment.environment.name']) = {environment:String} "
            "AND Attributes[{kind:String}] = {server:String} "
            "AND TimeUnix > fromUnixTimestamp64Milli(toInt64({start:Float64} * 1000)) "
            "AND TimeUnix <= fromUnixTimestamp64Milli(toInt64({end:Float64} * 1000))",
            {
                **self._parameters(scope),
                "metric": mapping.name,
                "status": mapping.status_attribute,
                "error": mapping.error_value,
                "kind": mapping.kind_attribute,
                "server": mapping.server_value,
            },
        )
        row = rows[0]
        if row["invalid"]:
            raise ValueError(
                "Request metric must contain nonnegative monotonic delta counters only."
            )
        return Measurement(float(row["total"]), float(row["errors"]), float(row["latest"]))

    def evidence(self, scope: Scope) -> Evidence:
        """Read at most ten span rows and ten logs, without exporting arbitrary attribute maps."""
        where = (
            " WHERE ServiceName = {service:String} "
            "AND ResourceAttributes['deployment.environment.name'] = {environment:String} "
            "AND Timestamp > fromUnixTimestamp64Milli(toInt64({start:Float64} * 1000)) "
            "AND Timestamp <= fromUnixTimestamp64Milli(toInt64({end:Float64} * 1000)) "
        )
        traces = self._query(
            "SELECT toString(Timestamp) AS timestamp, TraceId AS trace_id, SpanId AS span_id, "
            "SpanName AS operation, StatusCode AS status, "
            "substringUTF8(StatusMessage, 1, 400) AS message, Duration / 1e6 AS duration_ms "
            "FROM otel_traces"
            + where
            + "ORDER BY StatusCode = 'Error' DESC, Timestamp DESC LIMIT 10",
            self._parameters(scope),
        )
        logs = self._query(
            "SELECT toString(Timestamp) AS timestamp, TraceId AS trace_id, "
            "SeverityText AS severity, substringUTF8(Body, 1, 400) AS message "
            "FROM otel_logs" + where + "ORDER BY SeverityNumber DESC, Timestamp DESC LIMIT 10",
            self._parameters(scope),
        )
        return {"traces": traces, "logs": logs}
