"""Coralogix DataPrime client for RCA log queries."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.integrations.models import CoralogixIntegrationConfig

_DEFAULT_TIMEOUT_SECONDS = 30.0


def _escape_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _ensure_limit_clause(query: str, limit: int) -> str:
    normalized = query.strip()
    if " limit " in normalized.lower() or normalized.lower().endswith("| limit"):
        return normalized
    return f"{normalized} | limit {max(int(limit), 1)}"


def build_coralogix_logs_query(
    *,
    raw_query: str = "",
    application_name: str = "",
    subsystem_name: str = "",
    text_query: str = "",
    trace_id: str = "",
    limit: int = 50,
) -> str:
    """Build a Coralogix DataPrime log query with optional RCA-friendly filters."""
    if raw_query.strip():
        return _ensure_limit_clause(raw_query.strip(), limit)

    filters: list[str] = []
    if application_name:
        filters.append(f"$l.applicationname == '{_escape_query_value(application_name)}'")
    if subsystem_name:
        filters.append(f"$l.subsystemname == '{_escape_query_value(subsystem_name)}'")
    if trace_id:
        filters.append(f"$d.trace_id == '{_escape_query_value(trace_id)}'")
    if text_query:
        filters.append(f"$d.message.contains('{_escape_query_value(text_query)}')")

    query_parts = ["source logs"]
    query_parts.extend(f"filter {clause}" for clause in filters)
    query_parts.append(f"limit {max(int(limit), 1)}")
    return " | ".join(query_parts)


class CoralogixClient:
    """Synchronous Coralogix client backed by the DataPrime HTTP API."""

    def __init__(self, config: CoralogixIntegrationConfig) -> None:
        self.config = config

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_key and self.config.base_url)

    @property
    def query_url(self) -> str:
        return f"{self.config.base_url}/api/v1/dataprime/query"

    def _request_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _parse_ndjson(self, response_text: str) -> dict[str, Any]:
        query_ids: list[str] = []
        warnings: list[str] = []
        rows: list[dict[str, Any]] = []

        for line in response_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue

            if isinstance(payload, dict):
                query_id = payload.get("queryId")
                if isinstance(query_id, dict):
                    maybe_id = str(query_id.get("queryId", "")).strip()
                    if maybe_id:
                        query_ids.append(maybe_id)
                elif isinstance(query_id, str) and query_id.strip():
                    query_ids.append(query_id.strip())

                warning = payload.get("warning")
                if isinstance(warning, str) and warning.strip():
                    warnings.append(warning.strip())

                direct_results = payload.get("result", {})
                if isinstance(direct_results, dict):
                    result_rows = direct_results.get("results", [])
                    if isinstance(result_rows, list):
                        rows.extend(item for item in result_rows if isinstance(item, dict))

                background_results = payload.get("response", {})
                if isinstance(background_results, dict):
                    nested_results = background_results.get("results", {})
                    if isinstance(nested_results, dict):
                        result_rows = nested_results.get("results", [])
                        if isinstance(result_rows, list):
                            rows.extend(item for item in result_rows if isinstance(item, dict))

        return {
            "query_ids": query_ids,
            "warnings": warnings,
            "rows": rows,
        }

    @staticmethod
    def _items_to_dict(items: object) -> dict[str, Any]:
        if not isinstance(items, list):
            return {}
        result: dict[str, Any] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            if not key:
                continue
            result[key] = item.get("value")
        return result

    @staticmethod
    def _parse_user_data(value: object) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str) or not value.strip():
            return {}
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        metadata = self._items_to_dict(row.get("metadata"))
        labels = self._items_to_dict(row.get("labels"))
        user_data = self._parse_user_data(row.get("userData"))
        log_obj = user_data.get("log_obj", {}) if isinstance(user_data, dict) else {}
        if not isinstance(log_obj, dict):
            log_obj = {}

        message = str(
            log_obj.get("message") or user_data.get("message") or metadata.get("message") or ""
        ).strip()
        timestamp = str(
            log_obj.get("timestamp")
            or user_data.get("timestamp")
            or metadata.get("timestamp")
            or ""
        ).strip()
        level = str(
            log_obj.get("level") or user_data.get("level") or metadata.get("severity") or ""
        ).strip()
        trace_id = str(
            user_data.get("trace_id") or log_obj.get("trace_id") or metadata.get("trace_id") or ""
        ).strip()

        return {
            "timestamp": timestamp,
            "message": message,
            "level": level,
            "trace_id": trace_id,
            "application_name": str(labels.get("applicationname", "")).strip(),
            "subsystem_name": str(labels.get("subsystemname", "")).strip(),
            "labels": labels,
            "metadata": metadata,
            "user_data": user_data,
        }

    def query_logs(
        self,
        query: str,
        *,
        time_range_minutes: int = 60,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Run a Coralogix DataPrime direct query and normalize log rows."""
        query_with_limit = _ensure_limit_clause(query, limit)
        now = datetime.now(UTC)
        start = now - timedelta(minutes=max(int(time_range_minutes), 1))
        payload = {
            "query": query_with_limit,
            "metadata": {
                "startDate": start.isoformat(),
                "endDate": now.isoformat(),
                "defaultSource": "logs",
            },
        }

        try:
            response = httpx.post(
                self.query_url,
                headers=self._request_headers(),
                json=payload,
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}

        parsed = self._parse_ndjson(response.text)
        logs = [self._normalize_row(row) for row in parsed["rows"]]
        return {
            "success": True,
            "logs": logs,
            "total": len(logs),
            "query": query_with_limit,
            "query_ids": parsed["query_ids"],
            "warnings": parsed["warnings"],
        }

    def validate_access(self) -> dict[str, Any]:
        """Validate Coralogix access with a lightweight direct query."""
        result = self.query_logs("source logs | limit 1", time_range_minutes=15, limit=1)
        if not result.get("success"):
            return result
        return {
            "success": True,
            "total": result.get("total", 0),
            "warnings": result.get("warnings", []),
        }
