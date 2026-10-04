"""Mimir metrics query mixin for Grafana Cloud client."""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from integrations.grafana.base import GrafanaClientBase

_ERROR_DETAIL_MAX_CHARS = 300
#: A bare Prometheus metric name — the only shape a ``{service_name=...}``
#: selector can be appended to without producing invalid PromQL.
_BARE_METRIC_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")


def _error_detail(body: str) -> str:
    """Bounded error text from a Prometheus/Mimir error body (JSON or plain)."""
    text = body.strip()
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict) and payload.get("error"):
        error_type = payload.get("errorType")
        text = f"{error_type}: {payload['error']}" if error_type else str(payload["error"])
    return text[:_ERROR_DETAIL_MAX_CHARS]


def _describe_failure(status: int, body: str) -> str:
    """Error message for a failed Mimir response, carrying the vendor's reason."""
    message = f"Mimir query failed: {status}"
    detail = _error_detail(body)
    if detail:
        message = f"{message}: {detail}"
    if status == HTTPStatus.NOT_FOUND:
        message += (
            " (the Grafana datasource proxy path was not found: the configured Mimir "
            "datasource UID likely does not exist or is not a Prometheus-compatible "
            "datasource)"
        )
    return message


class MimirMixin:
    """Mixin providing Mimir metrics query capabilities."""

    def query_mimir(  # type: ignore[misc]
        self: GrafanaClientBase,
        metric_name: str,
        service_name: str | None = None,
    ) -> dict[str, Any]:
        """Query Grafana Cloud Mimir for metrics.

        Args:
            metric_name: Prometheus metric name (e.g., pipeline_runs_total)
            service_name: Optional service name filter

        Returns:
            Dictionary with metric series and values
        """
        if not self.is_configured:
            return {
                "success": False,
                "error": f"Grafana client not configured for account '{self.account_id}'",
                "metrics": [],
            }

        url = self._build_datasource_url(
            self.mimir_datasource_uid,
            "/api/v1/query",
        )

        query = metric_name
        if service_name:
            if not _BARE_METRIC_NAME.fullmatch(metric_name):
                # Running the expression unfiltered would report other services'
                # series as this service's; the filter belongs inside the PromQL.
                return {
                    "success": False,
                    "error": (
                        "service_name applies only to a bare metric name. For a PromQL "
                        "expression, put the matcher inside it, e.g. "
                        f'sum(rate(my_metric{{service_name="{service_name}"}}[5m])), '
                        "and omit service_name."
                    ),
                    "metrics": [],
                }
            query = f'{metric_name}{{service_name="{service_name}"}}'

        params = {"query": query}

        try:
            data = self._make_get_request(url, params=params)
            result = data.get("data", {}).get("result", [])

            metrics = []
            for series in result:
                metrics.append(
                    {
                        "metric": series.get("metric", {}),
                        "value": series.get("value", []),
                    }
                )

            return {
                "success": True,
                "metrics": metrics,
                "total_series": len(result),
                "query": query,
                "account_id": self.account_id,
            }
        except Exception as e:
            error_msg = str(e)
            response_text = ""
            if hasattr(e, "response") and e.response is not None:
                response_text = e.response.text[:_ERROR_DETAIL_MAX_CHARS]
                error_msg = _describe_failure(e.response.status_code, e.response.text)

            return {
                "success": False,
                "error": error_msg,
                "response": response_text,
                "metrics": [],
            }
