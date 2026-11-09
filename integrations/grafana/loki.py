"""Loki log query mixin for Grafana Cloud client."""

from __future__ import annotations

import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from integrations.grafana.base import GrafanaClientBase

_MAX_LOKI_LOG_LINES = 5000


def _describe_status(status: int) -> str:
    """Human-readable reason for a failed Loki response status."""
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return (
            f"Loki rate-limited the query ({status}). "
            "Retry later, shorten the time range, or lower the limit."
        )
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return f"Loki is unavailable ({status}). Server-side failure, retry later."
    return f"Loki query failed: {status}"


class LokiMixin:
    """Mixin providing Loki log query capabilities."""

    def query_loki(  # type: ignore[misc]
        self: GrafanaClientBase,
        query: str,
        time_range_minutes: int = 60,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Query Grafana Cloud Loki for logs.

        Args:
            query: LogQL query string (e.g., '{service_name="lambda-mock-dag"}')
            time_range_minutes: Time range in minutes (default 60)
            limit: Maximum number of log entries to return

        Returns:
            Dictionary with log streams and metadata
        """
        if not self.is_configured:
            return {
                "success": False,
                "error": f"Grafana client not configured for account '{self.account_id}'",
                "logs": [],
                "truncated": False,
            }

        url = self._build_datasource_url(
            self.loki_datasource_uid,
            "/loki/api/v1/query_range",
        )

        end_ns = int(time.time() * 1e9)
        start_ns = end_ns - (time_range_minutes * 60 * int(1e9))

        requested_limit = limit
        limit = min(limit, _MAX_LOKI_LOG_LINES)

        params: dict[str, str] = {
            "query": query,
            "limit": str(limit),
            "start": str(start_ns),
            "end": str(end_ns),
        }

        try:
            data = self._make_get_request(url, params=params)
            result = data.get("data", {}).get("result", [])

            logs: list[dict[str, Any]] = []
            truncated: bool = False

            for stream in result:
                stream_labels = stream.get("stream", {})
                values = stream.get("values", [])

                for timestamp_ns, log_line in values:
                    if len(logs) >= _MAX_LOKI_LOG_LINES:
                        truncated = True
                        break

                    logs.append(
                        {
                            "timestamp": timestamp_ns,
                            "message": log_line,
                            "labels": dict(stream_labels),
                        }
                    )

                if truncated:
                    break

            if requested_limit > _MAX_LOKI_LOG_LINES and len(logs) == _MAX_LOKI_LOG_LINES:
                truncated = True

            return {
                "success": True,
                "logs": logs,
                "truncated": truncated,
                "total_streams": len(result),
                "total_logs": len(logs),
                "query": query,
                "account_id": self.account_id,
            }
        except Exception as e:
            error_msg = str(e)
            response_text = ""
            if hasattr(e, "response") and e.response is not None:
                response_text = e.response.text[:300]
                error_msg = _describe_status(e.response.status_code)

            return {
                "success": False,
                "error": error_msg,
                "response": response_text,
                "logs": [],
                "truncated": False,
            }
