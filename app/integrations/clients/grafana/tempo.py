"""Tempo trace query mixin for Grafana Cloud client."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from app.integrations.clients.grafana.base import GrafanaClientBase

logger = logging.getLogger(__name__)


class TempoMixin:
    """Mixin providing Tempo trace query capabilities."""

    def query_tempo(  # type: ignore[misc]
        self: GrafanaClientBase,
        service_name: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Query Grafana Cloud Tempo for traces.

        Args:
            service_name: Service name to filter traces
            limit: Maximum number of traces to return

        Returns:
            Dictionary with traces and span details
        """
        if not self.is_configured:
            return {
                "success": False,
                "error": f"Grafana client not configured for account '{self.account_id}'",
                "traces": [],
            }

        url = self._build_datasource_url(
            self.tempo_datasource_uid,
            "/api/search",
        )

        params: dict[str, str] = {
            "q": f'{{.service.name="{service_name}"}}',
            "limit": str(limit),
        }

        try:
            data = self._make_request(url, params=params)
            traces = data.get("traces", [])

            enriched_traces = []
            for trace in traces:
                trace_id = trace.get("traceID", "")
                span_details = self._get_trace_details(trace_id)  # type: ignore[attr-defined]

                enriched_traces.append(
                    {
                        "trace_id": trace_id,
                        "root_service": trace.get("rootServiceName", ""),
                        "duration_ms": trace.get("durationMs", 0),
                        "span_count": trace.get("spanCount", 0),
                        "spans": span_details.get("spans", []),
                    }
                )

            return {
                "success": True,
                "traces": enriched_traces,
                "total_traces": len(traces),
                "service_name": service_name,
                "account_id": self.account_id,
            }
        except Exception as e:
            error_msg = str(e)
            response_text = ""
            if hasattr(e, "response") and e.response is not None:
                response_text = e.response.text[:300]
                error_msg = f"Tempo query failed: {e.response.status_code}"

            return {
                "success": False,
                "error": error_msg,
                "response": response_text,
                "traces": [],
            }

    def _get_trace_details(  # type: ignore[misc]
        self: GrafanaClientBase,
        trace_id: str,
    ) -> dict[str, Any]:
        """Get detailed span information for a trace.

        Args:
            trace_id: The trace ID to fetch details for

        Returns:
            Dictionary with spans list
        """
        url = self._build_datasource_url(
            self.tempo_datasource_uid,
            f"/api/traces/{trace_id}",
        )

        try:
            response = requests.get(
                url,
                headers=self._get_auth_headers(),
                timeout=10,
            )

            if response.status_code == 200:
                trace_data = response.json()
                spans = []

                if "batches" in trace_data:
                    for batch in trace_data["batches"]:
                        if "scopeSpans" in batch:
                            for scope in batch["scopeSpans"]:
                                if "spans" in scope:
                                    for span in scope["spans"]:
                                        attributes = self._extract_span_attributes(span)  # type: ignore[attr-defined]
                                        spans.append(
                                            {
                                                "name": span.get("name", "unknown"),
                                                "attributes": attributes,
                                            }
                                        )

                return {"spans": spans}
        except Exception:
            logger.debug("Failed to fetch Tempo trace spans", exc_info=True)
            return {"spans": []}

        return {"spans": []}

    def _extract_span_attributes(  # type: ignore[misc]
        self: GrafanaClientBase,
        span: dict[str, Any],
    ) -> dict[str, Any]:
        """Extract attributes from a span.

        Args:
            span: Span data dictionary

        Returns:
            Dictionary of attribute key-value pairs
        """
        attributes: dict[str, Any] = {}

        if "attributes" in span:
            for attr in span["attributes"]:
                key = attr.get("key", "")
                value = attr.get("value", {})

                if "stringValue" in value:
                    attributes[key] = value["stringValue"]
                elif "intValue" in value:
                    attributes[key] = value["intValue"]

        return attributes
