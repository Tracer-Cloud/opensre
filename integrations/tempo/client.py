"""Grafana Tempo query client.

Read-only access to a standalone Tempo backend via its HTTP API:

* ``GET /api/traces/{id}``  — fetch a full trace (OTLP/JSON)
* ``GET /api/search``       — search traces with TraceQL
* ``GET /api/v2/search/tag/{tag}/values`` — list services / span names
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from core.tool_framework.utils import tool_unavailable
from infrastructure.observability.otlp_parser import parse_otlp_trace
from integrations.tempo import TempoConfig

logger = logging.getLogger(__name__)

DEFAULT_TIME_RANGE_MINUTES = 60
_NOT_CONFIGURED_ERROR = "Tempo not configured. Set TEMPO_URL."

# Scoped tag names used by Tempo's tag-values endpoint.
_SERVICE_NAME_TAG = "resource.service.name"
_SPAN_NAME_TAG = "name"


_VALID_TAG_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _escape_traceql_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _time_bounds_seconds(minutes: int) -> tuple[int, int]:
    """Return (start, end) as unix seconds for the last *minutes*."""
    end = datetime.now(UTC)
    start = end - timedelta(minutes=max(1, minutes))
    return int(start.timestamp()), int(end.timestamp())


def _parse_tag_values(payload: dict[str, Any]) -> list[str]:
    """Parse a Tempo tag-values response (v1 strings or v2 typed objects)."""
    values: list[str] = []
    for item in payload.get("tagValues", []) or []:
        if isinstance(item, dict):
            value = item.get("value")
            if value:
                values.append(str(value))
        elif item:
            values.append(str(item))
    return values


def _parse_search_traces(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse the trace summaries returned by ``GET /api/search``."""
    results: list[dict[str, Any]] = []
    for trace in payload.get("traces", []) or []:
        if not isinstance(trace, dict):
            continue
        span_set = trace.get("spanSet") or {}
        matched = span_set.get("matched")
        if matched is None:
            matched = len(span_set.get("spans", []) or [])
        try:
            duration_ms = round(float(trace.get("durationMs", 0)), 4)
        except (TypeError, ValueError):
            duration_ms = 0
        results.append(
            {
                "trace_id": str(trace.get("traceID", "")),
                "root_service_name": str(trace.get("rootServiceName", "")),
                "root_trace_name": str(trace.get("rootTraceName", "")),
                "start_time_unix_nano": str(trace.get("startTimeUnixNano", "")),
                "duration_ms": duration_ms,
                "matched_spans": matched,
            }
        )
    return results


class TempoClient:
    """Read-only Grafana Tempo client over the HTTP API."""

    def __init__(self, config: TempoConfig) -> None:
        self.config = config

    def _configuration_error(self) -> str | None:
        if self.config.is_configured:
            return None
        return _NOT_CONFIGURED_ERROR

    def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            response = httpx.get(
                f"{self.config.base_url()}{path}",
                params=params,
                headers=self.config.auth_headers(),
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            parsed = response.json()
            if not isinstance(parsed, dict):
                return (
                    None,
                    f"Unexpected response shape: expected object, got {type(parsed).__name__}",
                )
            return parsed, None
        except httpx.HTTPStatusError as err:
            snippet = err.response.text[:200].strip()
            if snippet:
                return None, f"HTTP {err.response.status_code}: {snippet}"
            return None, f"HTTP {err.response.status_code}"
        except Exception as err:
            return None, str(err)

    def _clamped_limit(self, limit: int) -> int:
        return max(1, min(limit, self.config.max_results))

    # ----------------------------------------------------------- get by id

    def get_trace_by_id(self, trace_id: str) -> dict[str, Any]:
        """Fetch a full trace by ID and flatten it into spans."""
        config_error = self._configuration_error()
        if config_error:
            return tool_unavailable("tempo", config_error, action="get_trace", spans=[])
        if not trace_id:
            return tool_unavailable(
                "tempo", "trace_id is required for get_trace.", action="get_trace", spans=[]
            )

        payload, error = self._get(f"/api/traces/{trace_id}")
        if error:
            return tool_unavailable("tempo", error, action="get_trace", trace_id=trace_id, spans=[])

        spans = parse_otlp_trace(payload or {})
        return {
            "source": "tempo",
            "action": "get_trace",
            "available": True,
            "trace_id": trace_id,
            "total_spans": len(spans),
            "spans": spans,
        }

    # ------------------------------------------------------------- search

    def search_traces(
        self,
        service: str | None = None,
        span_name: str | None = None,
        min_duration_ms: float | None = None,
        max_duration_ms: float | None = None,
        tags: dict[str, str] | None = None,
        time_range_minutes: int = DEFAULT_TIME_RANGE_MINUTES,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search traces by service, span name, duration, and tags via TraceQL."""
        config_error = self._configuration_error()
        if config_error:
            return tool_unavailable("tempo", config_error, action="search", traces=[])

        traceql = self._build_traceql(
            service=service,
            span_name=span_name,
            min_duration_ms=min_duration_ms,
            max_duration_ms=max_duration_ms,
            tags=tags,
        )
        start, end = _time_bounds_seconds(time_range_minutes)
        params: dict[str, Any] = {
            "q": traceql,
            "limit": self._clamped_limit(limit),
            "start": start,
            "end": end,
        }

        payload, error = self._get("/api/search", params=params)
        if error:
            return tool_unavailable("tempo", error, action="search", query=traceql, traces=[])

        traces = _parse_search_traces(payload or {})
        return {
            "source": "tempo",
            "action": "search",
            "available": True,
            "query": traceql,
            "total": len(traces),
            "traces": traces,
        }

    @staticmethod
    def _build_traceql(
        *,
        service: str | None,
        span_name: str | None,
        min_duration_ms: float | None,
        max_duration_ms: float | None,
        tags: dict[str, str] | None,
    ) -> str:
        parts: list[str] = []
        if service:
            parts.append(f'resource.service.name = "{_escape_traceql_value(service)}"')
        if span_name:
            parts.append(f'name = "{_escape_traceql_value(span_name)}"')
        if min_duration_ms is not None and min_duration_ms > 0:
            parts.append(f"duration > {min_duration_ms}ms")
        if max_duration_ms is not None and max_duration_ms > 0:
            parts.append(f"duration < {max_duration_ms}ms")
        for key, value in (tags or {}).items():
            if not key or not _VALID_TAG_KEY_RE.match(key):
                continue
            # Honour explicit scope prefixes (resource./span.); default to span.
            if key.startswith("resource.") or key.startswith("span."):
                scoped_key = key
            else:
                scoped_key = f"span.{key}"
            parts.append(f'{scoped_key} = "{_escape_traceql_value(str(value))}"')
        if not parts:
            return "{}"
        return "{ " + " && ".join(parts) + " }"

    # ----------------------------------------------------- list tag values

    def list_services(self, time_range_minutes: int = DEFAULT_TIME_RANGE_MINUTES) -> dict[str, Any]:
        """List service names registered in Tempo."""
        return self._list_tag_values(
            tag=_SERVICE_NAME_TAG,
            result_key="services",
            time_range_minutes=time_range_minutes,
            action="list_services",
        )

    def list_span_names(
        self, time_range_minutes: int = DEFAULT_TIME_RANGE_MINUTES
    ) -> dict[str, Any]:
        """List span names registered in Tempo."""
        return self._list_tag_values(
            tag=_SPAN_NAME_TAG,
            result_key="span_names",
            time_range_minutes=time_range_minutes,
            action="list_span_names",
        )

    def _list_tag_values(
        self,
        *,
        tag: str,
        result_key: str,
        time_range_minutes: int,
        action: str,
    ) -> dict[str, Any]:
        config_error = self._configuration_error()
        if config_error:
            return tool_unavailable("tempo", config_error, action=action, **{result_key: []})

        start, end = _time_bounds_seconds(time_range_minutes)
        payload, error = self._get(
            f"/api/v2/search/tag/{tag}/values",
            params={"start": start, "end": end},
        )
        # Fall back to the v1 endpoint for Tempo deployments older than ~2.0.
        if error and "404" in error:
            payload, error = self._get(
                f"/api/search/tag/{tag}/values",
                params={"start": start, "end": end},
            )
        if error:
            return tool_unavailable("tempo", error, action=action, **{result_key: []})

        values = _parse_tag_values(payload or {})
        return {
            "source": "tempo",
            "action": action,
            "available": True,
            "total": len(values),
            result_key: values,
        }
