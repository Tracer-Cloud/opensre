"""Grafana Cloud API client for querying logs, traces, and metrics."""

from __future__ import annotations

import time
from typing import Any

import requests

from app.agent.tools.clients.grafana_config import GrafanaAccountConfig, get_grafana_config


class GrafanaClient:
    """Client for querying Grafana Cloud Loki, Tempo, and Mimir."""

    def __init__(
        self,
        account_id: str | None = None,
        config: GrafanaAccountConfig | None = None,
    ):
        """Initialize Grafana client.

        Args:
            account_id: Grafana account identifier (e.g., "tracerbio", "customer1").
                       If None, uses the default account from config.
            config: Optional pre-loaded config. If provided, account_id is ignored.
        """
        if config is not None:
            self._config = config
        else:
            self._config = get_grafana_config(account_id)

        self.account_id = self._config.account_id
        self.instance_url = self._config.instance_url
        self.read_token = self._config.read_token
        self.loki_datasource_uid = self._config.loki_datasource_uid
        self.tempo_datasource_uid = self._config.tempo_datasource_uid
        self.mimir_datasource_uid = self._config.mimir_datasource_uid

    @property
    def is_configured(self) -> bool:
        """Check if client is properly configured."""
        return self._config.is_configured

    def query_loki(
        self,
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
        if not self.read_token:
            return {
                "success": False,
                "error": f"Read token not configured for account '{self.account_id}'",
                "logs": [],
            }

        url = (
            f"{self.instance_url}/api/datasources/proxy/uid/"
            f"{self.loki_datasource_uid}/loki/api/v1/query_range"
        )
        headers = {"Authorization": f"Bearer {self.read_token}"}

        end_ns = int(time.time() * 1e9)
        start_ns = end_ns - (time_range_minutes * 60 * int(1e9))

        params: dict[str, str] = {
            "query": query,
            "limit": str(limit),
            "start": str(start_ns),
            "end": str(end_ns),
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                result = data.get("data", {}).get("result", [])

                logs = []
                for stream in result:
                    stream_labels = stream.get("stream", {})
                    values = stream.get("values", [])

                    for timestamp_ns, log_line in values:
                        logs.append(
                            {
                                "timestamp": timestamp_ns,
                                "message": log_line,
                                "labels": stream_labels,
                            }
                        )

                return {
                    "success": True,
                    "logs": logs,
                    "total_streams": len(result),
                    "total_logs": len(logs),
                    "query": query,
                    "account_id": self.account_id,
                }
            else:
                return {
                    "success": False,
                    "error": f"Loki query failed: {response.status_code}",
                    "response": response.text[:300],
                    "logs": [],
                }
        except Exception as e:
            return {"success": False, "error": str(e), "logs": []}

    def query_tempo(
        self,
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
        if not self.read_token:
            return {
                "success": False,
                "error": f"Read token not configured for account '{self.account_id}'",
                "traces": [],
            }

        url = (
            f"{self.instance_url}/api/datasources/proxy/uid/"
            f"{self.tempo_datasource_uid}/api/search"
        )
        headers = {"Authorization": f"Bearer {self.read_token}"}

        params: dict[str, str] = {
            "q": f'{{.service.name="{service_name}"}}',
            "limit": str(limit),
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                traces = data.get("traces", [])

                enriched_traces = []
                for trace in traces:
                    trace_id = trace.get("traceID", "")
                    span_details = self._get_trace_details(trace_id)

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
            else:
                return {
                    "success": False,
                    "error": f"Tempo query failed: {response.status_code}",
                    "response": response.text[:300],
                    "traces": [],
                }
        except Exception as e:
            return {"success": False, "error": str(e), "traces": []}

    def _get_trace_details(self, trace_id: str) -> dict[str, Any]:
        """Get detailed span information for a trace."""
        url = (
            f"{self.instance_url}/api/datasources/proxy/uid/"
            f"{self.tempo_datasource_uid}/api/traces/{trace_id}"
        )
        headers = {"Authorization": f"Bearer {self.read_token}"}

        try:
            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code == 200:
                trace_data = response.json()
                spans = []

                if "batches" in trace_data:
                    for batch in trace_data["batches"]:
                        if "scopeSpans" in batch:
                            for scope in batch["scopeSpans"]:
                                if "spans" in scope:
                                    for span in scope["spans"]:
                                        attributes = {}
                                        if "attributes" in span:
                                            for attr in span["attributes"]:
                                                key = attr.get("key", "")
                                                value = attr.get("value", {})

                                                if "stringValue" in value:
                                                    attributes[key] = value["stringValue"]
                                                elif "intValue" in value:
                                                    attributes[key] = value["intValue"]

                                        spans.append(
                                            {
                                                "name": span.get("name", "unknown"),
                                                "attributes": attributes,
                                            }
                                        )

                return {"spans": spans}
        except Exception:
            pass

        return {"spans": []}

    def query_mimir(
        self,
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
        if not self.read_token:
            return {
                "success": False,
                "error": f"Read token not configured for account '{self.account_id}'",
                "metrics": [],
            }

        url = (
            f"{self.instance_url}/api/datasources/proxy/uid/"
            f"{self.mimir_datasource_uid}/api/v1/query"
        )
        headers = {"Authorization": f"Bearer {self.read_token}"}

        query = metric_name
        if service_name:
            query = f'{metric_name}{{service_name="{service_name}"}}'

        params = {"query": query}

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
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
            else:
                return {
                    "success": False,
                    "error": f"Mimir query failed: {response.status_code}",
                    "response": response.text[:300],
                    "metrics": [],
                }
        except Exception as e:
            return {"success": False, "error": str(e), "metrics": []}


def get_grafana_client(account_id: str | None = None) -> GrafanaClient:
    """Get Grafana client for a specific account.

    Args:
        account_id: Grafana account identifier. If None, uses default account.

    Returns:
        GrafanaClient configured for the requested account
    """
    return GrafanaClient(account_id=account_id)
