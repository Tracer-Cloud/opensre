"""SigNoz query client.

Queries logs, metrics, and traces via SigNoz Query Range API
(``POST /api/v5/query_range``).
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any, cast

import httpx

from core.tool_framework.utils import tool_unavailable
from integrations.signoz import SigNozConfig

logger = logging.getLogger(__name__)

DEFAULT_TIME_RANGE_MINUTES = 60
# Best-effort metadata lookup: cheap enough that it should never wait as long as the
# actual query -- capped well below the configured query timeout so a slow/unreachable
# metadata endpoint can't add its full timeout on top of every metrics call.
_METADATA_LOOKUP_TIMEOUT_SECONDS = 2.0
_NOT_CONFIGURED_ERROR = (
    "SigNoz not configured. Set SIGNOZ_URL and SIGNOZ_API_KEY (service account key)."
)

# Curated infrastructure metrics for V1.
_CURATED_METRICS: dict[str, str] = {
    "cpu_usage": "system_cpu_usage",
    "memory_usage": "system_memory_usage",
    # NOTE: error_rate is intentionally omitted — signoz_calls_total counts all
    # requests regardless of status.  Use a raw metric name with a label filter
    # or query signoz_traces directly for error-rate semantics.
    "request_rate": "signoz_calls_total",
}


def _clamp_limit(limit: int, config: SigNozConfig) -> int:
    return max(1, min(limit, config.max_results))


def _time_bounds(minutes: int) -> tuple[datetime, datetime]:
    """Return (start, end) datetimes for the last *minutes*."""
    end = datetime.now(UTC)
    start = end - timedelta(minutes=max(1, minutes))
    return start, end


def _iso_from_epoch_ms(timestamp_ms: int) -> str:
    """Render epoch milliseconds as an ISO-8601 UTC timestamp."""
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _escape_signoz_filter_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _signoz_filter_expression(parts: list[str]) -> dict[str, str] | None:
    if not parts:
        return None
    return {"expression": " AND ".join(parts)}


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _field_from_data(data: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _extract_http_error_message(err: httpx.HTTPStatusError) -> str:
    error_payload: dict[str, Any] = {}
    try:
        parsed = err.response.json()
        if isinstance(parsed, dict):
            error_payload = parsed
    except Exception:
        error_payload = {}
    nested = error_payload.get("error")
    if isinstance(nested, dict):
        message = nested.get("message")
        if message:
            return str(message)
    snippet = err.response.text[:200].strip()
    return snippet or f"HTTP {err.response.status_code}"


def _row_data(row: dict[str, Any]) -> dict[str, Any]:
    nested = row.get("data")
    if isinstance(nested, dict):
        return cast(dict[str, Any], nested)
    return row


def _parse_log_row(row: dict[str, Any]) -> dict[str, Any]:
    data = _row_data(row)
    attributes_raw = data.get("attributes_string") or {}
    resources_raw = data.get("resources_string") or {}
    attributes = dict(attributes_raw) if isinstance(attributes_raw, dict) else {}
    resources = dict(resources_raw) if isinstance(resources_raw, dict) else {}
    return {
        "timestamp": _coerce_str(row.get("timestamp") or data.get("timestamp")),
        "severity": _coerce_str(_field_from_data(data, "severity_text")),
        "severity_number": _field_from_data(data, "severity_number", default=0),
        "message": _coerce_str(_field_from_data(data, "body")),
        "trace_id": _coerce_str(_field_from_data(data, "trace_id", "traceID")),
        "span_id": _coerce_str(_field_from_data(data, "span_id", "spanID")),
        "attributes": dict(attributes) if isinstance(attributes, dict) else {},
        "resources": dict(resources) if isinstance(resources, dict) else {},
    }


def _parse_trace_row(row: dict[str, Any]) -> dict[str, Any]:
    data = _row_data(row)
    duration_nano = _field_from_data(data, "duration_nano", "durationNano", default=0)
    try:
        duration_ms = float(duration_nano) / 1_000_000
    except (TypeError, ValueError):
        duration_ms = 0.0
    has_error = _field_from_data(data, "has_error", "hasError", default=False)
    return {
        "timestamp": _coerce_str(row.get("timestamp") or data.get("timestamp")),
        "trace_id": _coerce_str(_field_from_data(data, "trace_id", "traceID")),
        "span_id": _coerce_str(_field_from_data(data, "span_id", "spanID")),
        "name": _coerce_str(_field_from_data(data, "name")),
        "duration_ms": duration_ms,
        "has_error": bool(has_error),
        "status_code": _field_from_data(data, "status_code", "statusCode", default=0),
        "status_code_string": _coerce_str(
            _field_from_data(data, "status_code_string", "statusCodeString")
        ),
        "http_method": _coerce_str(_field_from_data(data, "http_method", "httpMethod")),
        "http_url": _coerce_str(_field_from_data(data, "http_url", "httpUrl")),
        "kind_string": _coerce_str(_field_from_data(data, "kind_string", "kindString")),
        "service_name": _coerce_str(_field_from_data(data, "service_name", "serviceName")),
    }


class SigNozClient:
    """Read-only SigNoz client using the Query Range API."""

    def __init__(self, config: SigNozConfig) -> None:
        self.config = config

    def _configuration_error(self) -> str | None:
        if self.config.is_configured:
            return None
        return _NOT_CONFIGURED_ERROR

    def _query_api_base_url(self) -> str:
        return self.config.url.rstrip("/")

    def _resolve_metric_temporality(self, metric_name: str) -> str:
        """Look up a metric's real temporality; callers must query with the
        temporality it was ingested with, or the backend returns zero rows."""
        try:
            response = httpx.get(
                f"{self._query_api_base_url()}/api/v2/metrics/metadata",
                params={"metricName": metric_name},
                headers={"SigNoz-Api-Key": self.config.api_key, "Accept": "application/json"},
                timeout=min(_METADATA_LOOKUP_TIMEOUT_SECONDS, self.config.timeout_seconds),
            )
            response.raise_for_status()
            return str(response.json().get("data", {}).get("temporality") or "unspecified")
        except Exception:
            return "unspecified"

    def _query_range_post(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            response = httpx.post(
                f"{self._query_api_base_url()}/api/v5/query_range",
                headers={
                    "SigNoz-Api-Key": self.config.api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            parsed = response.json()
            return (parsed if isinstance(parsed, dict) else {}), None
        except httpx.HTTPStatusError as err:
            message = _extract_http_error_message(err)
            if err.response.status_code == HTTPStatus.NOT_FOUND:
                return None, f"{HTTPStatus.NOT_FOUND}: {message or 'not found'}"
            return None, message
        except Exception as err:
            return None, str(err)

    @staticmethod
    def _unwrap_v5_query_data(response_json: dict[str, Any]) -> dict[str, Any]:
        query_response = response_json.get("data", {})
        if (
            isinstance(query_response, dict)
            and "type" in query_response
            and "data" in query_response
        ):
            inner = query_response.get("data", {})
            return inner if isinstance(inner, dict) else {}
        return query_response if isinstance(query_response, dict) else {}

    @staticmethod
    def _parse_raw_rows(response_json: dict[str, Any]) -> list[dict[str, Any]]:
        query_data = SigNozClient._unwrap_v5_query_data(response_json)
        results = query_data.get("results", []) if isinstance(query_data, dict) else []
        rows: list[dict[str, Any]] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            for row in result.get("rows") or []:
                if isinstance(row, dict):
                    rows.append(row)
        return rows

    @staticmethod
    def _parse_scalar_by_query_name(response_json: dict[str, Any]) -> dict[str, float]:
        query_data = SigNozClient._unwrap_v5_query_data(response_json)
        results = query_data.get("results", []) if isinstance(query_data, dict) else []
        values_by_query: dict[str, float] = {}
        for result in results:
            if not isinstance(result, dict):
                continue
            columns = result.get("columns") or []
            data_rows = result.get("data") or []
            if not data_rows or not isinstance(data_rows[0], list):
                continue
            first_row = data_rows[0]
            for idx, column in enumerate(columns):
                if not isinstance(column, dict):
                    continue
                query_name = str(column.get("queryName") or "")
                if not query_name or idx >= len(first_row):
                    continue
                try:
                    values_by_query[query_name] = float(first_row[idx])
                except (TypeError, ValueError):
                    values_by_query[query_name] = 0.0
        return values_by_query

    def _query_metrics_via_api(
        self,
        *,
        metric_name: str,
        resolved_metric: str,
        service: str | None,
        start: datetime,
        end: datetime,
        aggregation: str,
        effective_limit: int,
    ) -> dict[str, Any]:
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        step_interval = max(60, (end_ms - start_ms) // (1000 * 300))
        temporality = self._resolve_metric_temporality(resolved_metric)

        if resolved_metric == "signoz_calls_total":
            time_aggregation = "rate"
            space_aggregation = "sum"
        else:
            time_aggregation = (
                aggregation if aggregation in {"sum", "avg", "min", "max", "count"} else "avg"
            )
            space_aggregation = (
                aggregation if aggregation in {"sum", "avg", "min", "max", "count"} else "avg"
            )

        payload: dict[str, Any] = {
            "start": start_ms,
            "end": end_ms,
            "requestType": "time_series",
            "compositeQuery": {
                "queries": [
                    {
                        "type": "builder_query",
                        "spec": {
                            "name": "A",
                            "signal": "metrics",
                            "stepInterval": step_interval,
                            "aggregations": [
                                {
                                    "metricName": resolved_metric,
                                    "temporality": temporality,
                                    "timeAggregation": time_aggregation,
                                    "spaceAggregation": space_aggregation,
                                }
                            ],
                            "groupBy": [{"name": "service.name"}],
                            "disabled": False,
                            "limit": effective_limit,
                        },
                    }
                ]
            },
            "noCache": True,
        }
        if service:
            metric_filter = _signoz_filter_expression(
                [f"service.name = '{_escape_signoz_filter_value(service)}'"]
            )
            if metric_filter is not None:
                payload["compositeQuery"]["queries"][0]["spec"]["filter"] = metric_filter

        response_json, error_message = self._query_range_post(payload)
        if error_message:
            if "not found" in error_message.lower() or str(HTTPStatus.NOT_FOUND) in error_message:
                return {
                    "source": "signoz_metrics",
                    "available": True,
                    "total": 0,
                    "metric_name": metric_name,
                    "resolved_metric": resolved_metric,
                    "aggregation": aggregation,
                    "metrics": [],
                    "query_backend": "signoz_query_api",
                    "warning": error_message or f"Metric not found: {resolved_metric}",
                }
            return tool_unavailable(
                "signoz_metrics",
                error_message,
                metric_name=metric_name,
                resolved_metric=resolved_metric,
                aggregation=aggregation,
                metrics=[],
                query_backend="signoz_query_api",
            )

        response_json = response_json or {}
        query_data = self._unwrap_v5_query_data(response_json)
        results = query_data.get("results", []) if isinstance(query_data, dict) else []

        metrics: list[dict[str, Any]] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            aggregations = result.get("aggregations") or []
            for aggregation_bucket in aggregations:
                if not isinstance(aggregation_bucket, dict):
                    continue
                series_list = aggregation_bucket.get("series") or []
                for series in series_list:
                    if not isinstance(series, dict):
                        continue
                    labels = series.get("labels", [])
                    service_name = ""
                    for label in labels:
                        if not isinstance(label, dict):
                            continue
                        key = label.get("key", {})
                        key_name = key.get("name") if isinstance(key, dict) else ""
                        if key_name in {"service.name", "service_name"}:
                            service_name = str(label.get("value") or "")
                            break

                    values = series.get("values") or []
                    for point in values:
                        if not isinstance(point, dict):
                            continue
                        timestamp_ms = int(point.get("timestamp") or 0)
                        value = point.get("value")
                        metrics.append(
                            {
                                "interval": _iso_from_epoch_ms(timestamp_ms)
                                if timestamp_ms
                                else "",
                                "value": value,
                                "metric_name": resolved_metric,
                                "service_name": service_name,
                            }
                        )
                        if len(metrics) >= effective_limit:
                            break
                    if len(metrics) >= effective_limit:
                        break
                if len(metrics) >= effective_limit:
                    break
            if len(metrics) >= effective_limit:
                break

        return {
            "source": "signoz_metrics",
            "available": True,
            "total": len(metrics),
            "metric_name": metric_name,
            "resolved_metric": resolved_metric,
            "aggregation": aggregation,
            "metrics": metrics,
            "query_backend": "signoz_query_api",
            "effective_limit": effective_limit,
        }

    def _query_logs_via_api(
        self,
        *,
        service: str | None,
        start: datetime,
        end: datetime,
        severity: str | None,
        effective_limit: int,
    ) -> dict[str, Any]:
        filter_parts: list[str] = []
        if service:
            filter_parts.append(f"service.name = '{_escape_signoz_filter_value(service)}'")
        if severity:
            filter_parts.append(
                f"severity_text = '{_escape_signoz_filter_value(severity.upper())}'"
            )

        spec: dict[str, Any] = {
            "name": "A",
            "signal": "logs",
            "order": [
                {"key": {"name": "timestamp"}, "direction": "desc"},
                {"key": {"name": "id"}, "direction": "desc"},
            ],
            "offset": 0,
            "limit": effective_limit,
            "disabled": False,
        }
        log_filter = _signoz_filter_expression(filter_parts)
        if log_filter is not None:
            spec["filter"] = log_filter

        payload: dict[str, Any] = {
            "start": int(start.timestamp() * 1000),
            "end": int(end.timestamp() * 1000),
            "requestType": "raw",
            "compositeQuery": {"queries": [{"type": "builder_query", "spec": spec}]},
            "noCache": True,
        }

        response_json, error_message = self._query_range_post(payload)
        if error_message:
            return tool_unavailable(
                "signoz_logs", error_message, total=0, logs=[], query_backend="signoz_query_api"
            )

        logs = [_parse_log_row(row) for row in self._parse_raw_rows(response_json or {})]
        return {
            "source": "signoz_logs",
            "available": True,
            "total": len(logs),
            "logs": logs,
            "query_backend": "signoz_query_api",
            "effective_limit": effective_limit,
        }

    def _query_traces_via_api(
        self,
        *,
        service: str | None,
        start: datetime,
        end: datetime,
        error_only: bool,
        effective_limit: int,
    ) -> dict[str, Any]:
        filter_parts: list[str] = []
        if service:
            filter_parts.append(f"serviceName = '{_escape_signoz_filter_value(service)}'")
        if error_only:
            filter_parts.append("hasError = true")

        spec: dict[str, Any] = {
            "name": "A",
            "signal": "traces",
            "selectFields": [
                {"name": "serviceName"},
                {"name": "name"},
                {"name": "traceID"},
                {"name": "spanID"},
                {"name": "durationNano"},
                {"name": "hasError"},
                {"name": "statusCode"},
                {"name": "statusCodeString"},
                {"name": "httpMethod"},
                {"name": "httpUrl"},
                {"name": "kindString"},
            ],
            "order": [{"key": {"name": "timestamp"}, "direction": "desc"}],
            "offset": 0,
            "limit": effective_limit,
            "disabled": False,
        }
        trace_filter = _signoz_filter_expression(filter_parts)
        if trace_filter is not None:
            spec["filter"] = trace_filter

        payload: dict[str, Any] = {
            "start": int(start.timestamp() * 1000),
            "end": int(end.timestamp() * 1000),
            "requestType": "raw",
            "compositeQuery": {"queries": [{"type": "builder_query", "spec": spec}]},
            "noCache": True,
        }

        response_json, error_message = self._query_range_post(payload)
        if error_message:
            return tool_unavailable(
                "signoz_traces", error_message, total=0, traces=[], query_backend="signoz_query_api"
            )

        traces = [_parse_trace_row(row) for row in self._parse_raw_rows(response_json or {})]
        return {
            "source": "signoz_traces",
            "available": True,
            "total": len(traces),
            "traces": traces,
            "query_backend": "signoz_query_api",
            "effective_limit": effective_limit,
        }

    def _query_trace_summary_via_api(
        self,
        *,
        service: str | None,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        filter_parts: list[str] = []
        if service:
            filter_parts.append(f"service.name = '{_escape_signoz_filter_value(service)}'")
        base_filter = _signoz_filter_expression(filter_parts)

        error_filter_parts = list(filter_parts)
        error_filter_parts.append("has_error = true")
        error_filter = _signoz_filter_expression(error_filter_parts)

        def _trace_scalar_spec(
            name: str, expression: str, trace_filter: dict[str, str] | None
        ) -> dict[str, Any]:
            spec: dict[str, Any] = {
                "name": name,
                "signal": "traces",
                "stepInterval": 60,
                "aggregations": [{"expression": expression}],
                "disabled": False,
            }
            if trace_filter is not None:
                spec["filter"] = trace_filter
            return {"type": "builder_query", "spec": spec}

        payload: dict[str, Any] = {
            "start": int(start.timestamp() * 1000),
            "end": int(end.timestamp() * 1000),
            "requestType": "scalar",
            "compositeQuery": {
                "queries": [
                    _trace_scalar_spec("A", "count()", base_filter),
                    _trace_scalar_spec("B", "count()", error_filter),
                    _trace_scalar_spec("C", "p99(duration_nano)", base_filter),
                    _trace_scalar_spec("D", "p95(duration_nano)", base_filter),
                    _trace_scalar_spec("E", "avg(duration_nano)", base_filter),
                    _trace_scalar_spec("F", "max(duration_nano)", base_filter),
                ]
            },
            "noCache": True,
        }

        response_json, error_message = self._query_range_post(payload)
        if error_message:
            return tool_unavailable(
                "signoz_traces", error_message, query_backend="signoz_query_api"
            )

        values = self._parse_scalar_by_query_name(response_json or {})

        def _nano_to_ms(value: float) -> float:
            return round(value / 1_000_000, 4)

        def _safe_float(value: float, default: float = 0.0) -> float:
            try:
                parsed = float(value)
                return parsed if not math.isnan(parsed) else default
            except (TypeError, ValueError):
                return default

        total = int(values.get("A", 0))
        errors = int(values.get("B", 0))
        return {
            "source": "signoz_traces",
            "available": True,
            "total_spans": total,
            "error_spans": errors,
            "error_rate": round(errors / total, 4) if total else 0.0,
            "p99_ms": _nano_to_ms(_safe_float(values.get("C", 0.0))),
            "p95_ms": _nano_to_ms(_safe_float(values.get("D", 0.0))),
            "avg_ms": _nano_to_ms(_safe_float(values.get("E", 0.0))),
            "max_ms": _nano_to_ms(_safe_float(values.get("F", 0.0))),
            "query_backend": "signoz_query_api",
        }

    # ------------------------------------------------------------------ logs

    def query_logs(
        self,
        service: str | None = None,
        time_range_minutes: int = DEFAULT_TIME_RANGE_MINUTES,
        severity: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Query SigNoz logs via Query Range API."""
        config_error = self._configuration_error()
        if config_error:
            return tool_unavailable("signoz_logs", config_error, total=0, logs=[])

        effective_limit = _clamp_limit(limit, self.config)
        start, end = _time_bounds(time_range_minutes)
        return self._query_logs_via_api(
            service=service,
            start=start,
            end=end,
            severity=severity,
            effective_limit=effective_limit,
        )

    # ---------------------------------------------------------------- metrics

    def query_metrics(
        self,
        metric_name: str,
        service: str | None = None,
        time_range_minutes: int = DEFAULT_TIME_RANGE_MINUTES,
        aggregation: str = "avg",
        limit: int = 50,
    ) -> dict[str, Any]:
        """Query SigNoz metrics via Query Range API."""
        resolved_metric = _CURATED_METRICS.get(metric_name, metric_name)
        config_error = self._configuration_error()
        if config_error:
            return tool_unavailable(
                "signoz_metrics",
                config_error,
                metric_name=metric_name,
                resolved_metric=resolved_metric,
                aggregation=aggregation,
                metrics=[],
            )

        effective_limit = _clamp_limit(limit, self.config)
        start, end = _time_bounds(time_range_minutes)
        return self._query_metrics_via_api(
            metric_name=metric_name,
            resolved_metric=resolved_metric,
            service=service,
            start=start,
            end=end,
            aggregation=aggregation,
            effective_limit=effective_limit,
        )

    # ---------------------------------------------------------------- traces

    def query_traces(
        self,
        service: str | None = None,
        time_range_minutes: int = DEFAULT_TIME_RANGE_MINUTES,
        error_only: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Query SigNoz traces via Query Range API."""
        config_error = self._configuration_error()
        if config_error:
            return tool_unavailable("signoz_traces", config_error, total=0, traces=[])

        effective_limit = _clamp_limit(limit, self.config)
        start, end = _time_bounds(time_range_minutes)
        return self._query_traces_via_api(
            service=service,
            start=start,
            end=end,
            error_only=error_only,
            effective_limit=effective_limit,
        )

    # ---------------------------------------------------------------- summary

    def query_trace_summary(
        self,
        service: str | None = None,
        time_range_minutes: int = DEFAULT_TIME_RANGE_MINUTES,
    ) -> dict[str, Any]:
        """Return aggregate trace stats (error rate, p99 latency, call count)."""
        config_error = self._configuration_error()
        if config_error:
            return tool_unavailable("signoz_traces", config_error)

        start, end = _time_bounds(time_range_minutes)
        return self._query_trace_summary_via_api(service=service, start=start, end=end)
