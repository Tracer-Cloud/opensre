"""Honeycomb API client for RCA query helpers."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.integrations.models import HoneycombIntegrationConfig

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_POLL_ATTEMPTS = 10
_DEFAULT_POLL_INTERVAL_SECONDS = 0.5


class HoneycombClient:
    """Synchronous Honeycomb client for validation and query execution."""

    def __init__(self, config: HoneycombIntegrationConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                headers={
                    "X-Honeycomb-Team": self.config.api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
        return self._client

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_key and self.config.dataset)

    def validate_access(self) -> dict[str, Any]:
        """Validate the Honeycomb API key against the auth endpoint."""
        try:
            response = self._get_client().get("/1/auth")
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}

        environment = payload.get("environment", {}) if isinstance(payload, dict) else {}
        team = payload.get("team", {}) if isinstance(payload, dict) else {}
        return {
            "success": True,
            "environment": environment,
            "team": team,
            "key_type": str(payload.get("type", "")).strip() if isinstance(payload, dict) else "",
        }

    def create_query(self, query: dict[str, Any]) -> dict[str, Any]:
        """Create a Honeycomb query specification."""
        try:
            response = self._get_client().post(f"/1/queries/{self.config.dataset}", json=query)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}

        query_id = str(payload.get("id", "")).strip() if isinstance(payload, dict) else ""
        if not query_id:
            return {"success": False, "error": "Honeycomb query creation returned no query ID."}
        return {"success": True, "query_id": query_id, "query": payload}

    def create_query_result(self, query_id: str, *, limit: int) -> dict[str, Any]:
        """Run a previously created query and return the query-result envelope."""
        payload = {
            "query_id": query_id,
            "disable_series": True,
            "disable_total_by_aggregate": True,
            "disable_other_by_aggregate": True,
            "limit": limit,
        }
        try:
            response = self._get_client().post(
                f"/1/query_results/{self.config.dataset}",
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPStatusError as exc:
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}

        return {"success": True, "result": result}

    def get_query_result(self, query_result_id: str) -> dict[str, Any]:
        """Fetch an existing query result."""
        try:
            response = self._get_client().get(
                f"/1/query_results/{self.config.dataset}/{query_result_id}"
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
        return {"success": True, "result": payload}

    def run_query(
        self,
        query: dict[str, Any],
        *,
        limit: int = 20,
        poll_attempts: int = _DEFAULT_POLL_ATTEMPTS,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> dict[str, Any]:
        """Create, execute, and poll a Honeycomb query result until completion."""
        created_query = self.create_query(query)
        if not created_query.get("success"):
            return created_query

        query_id = str(created_query.get("query_id", "")).strip()
        created_result = self.create_query_result(query_id, limit=limit)
        if not created_result.get("success"):
            return created_result

        result = created_result.get("result", {})
        query_result_id = str(result.get("id", "")).strip() if isinstance(result, dict) else ""
        if not query_result_id:
            return {"success": False, "error": "Honeycomb query result returned no result ID."}

        current_result = result
        for _ in range(max(poll_attempts, 1)):
            if current_result.get("complete") is True:
                break
            time.sleep(max(poll_interval_seconds, 0.0))
            fetched = self.get_query_result(query_result_id)
            if not fetched.get("success"):
                return fetched
            fetched_result = fetched.get("result", {})
            if isinstance(fetched_result, dict):
                current_result = fetched_result
        else:
            return {
                "success": False,
                "error": "Honeycomb query result did not complete before the timeout.",
            }

        data = current_result.get("data", {}) if isinstance(current_result, dict) else {}
        raw_results = data.get("results", []) if isinstance(data, dict) else []
        results = [
            item.get("data", {})
            for item in raw_results
            if isinstance(item, dict) and isinstance(item.get("data"), dict)
        ]
        links = current_result.get("links", {}) if isinstance(current_result, dict) else {}

        return {
            "success": True,
            "query_id": query_id,
            "query_result_id": query_result_id,
            "query": query,
            "results": results,
            "raw_result": current_result,
            "query_url": str(links.get("query_url", "")).strip(),
            "graph_image_url": str(links.get("graph_image_url", "")).strip(),
        }

    def query_traces(
        self,
        *,
        service_name: str = "",
        trace_id: str = "",
        time_range_seconds: int = 3600,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Query Honeycomb trace/span groups for a service or trace ID."""
        filters: list[dict[str, Any]] = []
        if service_name:
            filters.append({"column": "service.name", "op": "=", "value": service_name})
        if trace_id:
            filters.append({"column": "trace.trace_id", "op": "=", "value": trace_id})

        if not filters:
            return {
                "success": False,
                "error": "Honeycomb trace queries require a service_name or trace_id.",
            }

        query = {
            "calculations": [
                {"op": "COUNT"},
                {"op": "MAX", "column": "duration_ms"},
                {"op": "AVG", "column": "duration_ms"},
            ],
            "breakdowns": ["trace.trace_id", "service.name", "name"],
            "filters": filters,
            "filter_combination": "AND",
            "orders": [{"op": "MAX", "column": "duration_ms", "order": "descending"}],
            "time_range": max(int(time_range_seconds), 60),
        }
        return self.run_query(query, limit=limit)
