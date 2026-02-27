"""Datadog API client for querying logs, events, and monitors.

Uses the Datadog REST API directly via httpx (no SDK dependency).
Credentials come from the user's Datadog integration stored in the Tracer web app DB.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30


@dataclass
class DatadogConfig:
    api_key: str
    app_key: str
    site: str = "datadoghq.com"

    @property
    def base_url(self) -> str:
        return f"https://api.{self.site}"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "DD-API-KEY": self.api_key,
            "DD-APPLICATION-KEY": self.app_key,
            "Content-Type": "application/json",
        }


class DatadogClient:
    """Synchronous client for querying Datadog logs, events, and monitors."""

    def __init__(self, config: DatadogConfig) -> None:
        self.config = config
        self._client = httpx.Client(
            base_url=config.base_url,
            headers=config.headers,
            timeout=_DEFAULT_TIMEOUT,
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_key and self.config.app_key)

    def search_logs(
        self,
        query: str,
        time_range_minutes: int = 60,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search Datadog logs using the Log Search API (v2)."""
        now = datetime.now(UTC)
        from_ts = now - timedelta(minutes=time_range_minutes)

        payload = {
            "filter": {
                "query": query,
                "from": from_ts.isoformat(),
                "to": now.isoformat(),
            },
            "sort": "-timestamp",
            "page": {"limit": min(limit, 1000)},
        }

        try:
            resp = self._client.post("/api/v2/logs/events/search", json=payload)
            resp.raise_for_status()
            data = resp.json()

            logs = []
            for event in data.get("data", []):
                attrs = event.get("attributes", {})
                logs.append({
                    "timestamp": attrs.get("timestamp", ""),
                    "message": attrs.get("message", ""),
                    "status": attrs.get("status", ""),
                    "service": attrs.get("service", ""),
                    "host": attrs.get("host", ""),
                    "tags": attrs.get("tags", []),
                })

            return {"success": True, "logs": logs, "total": len(logs)}
        except httpx.HTTPStatusError as e:
            logger.warning("[datadog] Log search failed: %s", e.response.status_code)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[datadog] Log search error: %s", e)
            return {"success": False, "error": str(e)}

    def list_monitors(
        self,
        query: str | None = None,
    ) -> dict[str, Any]:
        """List Datadog monitors, optionally filtered by query."""
        params: dict[str, Any] = {"page": 0, "page_size": 50}
        if query:
            params["query"] = query

        try:
            resp = self._client.get("/api/v1/monitor", params=params)
            resp.raise_for_status()
            monitors = resp.json()

            results = []
            for m in monitors if isinstance(monitors, list) else []:
                results.append({
                    "id": m.get("id"),
                    "name": m.get("name", ""),
                    "type": m.get("type", ""),
                    "query": m.get("query", ""),
                    "message": m.get("message", ""),
                    "overall_state": m.get("overall_state", ""),
                    "tags": m.get("tags", []),
                })

            return {"success": True, "monitors": results, "total": len(results)}
        except httpx.HTTPStatusError as e:
            logger.warning("[datadog] List monitors failed: %s", e.response.status_code)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[datadog] List monitors error: %s", e)
            return {"success": False, "error": str(e)}

    def get_events(
        self,
        query: str | None = None,
        time_range_minutes: int = 60,
    ) -> dict[str, Any]:
        """Query Datadog events (v2 API)."""
        now = datetime.now(UTC)
        from_ts = now - timedelta(minutes=time_range_minutes)

        payload: dict[str, Any] = {
            "filter": {
                "from": from_ts.isoformat(),
                "to": now.isoformat(),
            },
            "sort": "-timestamp",
            "page": {"limit": 50},
        }
        if query:
            payload["filter"]["query"] = query

        try:
            resp = self._client.post("/api/v2/events/search", json=payload)
            resp.raise_for_status()
            data = resp.json()

            events = []
            for event in data.get("data", []):
                attrs = event.get("attributes", {})
                events.append({
                    "timestamp": attrs.get("timestamp", ""),
                    "title": attrs.get("title", ""),
                    "message": attrs.get("message", attrs.get("text", "")),
                    "tags": attrs.get("tags", []),
                    "source": attrs.get("source", ""),
                })

            return {"success": True, "events": events, "total": len(events)}
        except httpx.HTTPStatusError as e:
            logger.warning("[datadog] Events query failed: %s", e.response.status_code)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[datadog] Events query error: %s", e)
            return {"success": False, "error": str(e)}


class DatadogAsyncClient:
    """Async client that fetches logs, monitors, and events in parallel."""

    def __init__(self, config: DatadogConfig) -> None:
        self.config = config

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_key and self.config.app_key)

    async def _search_logs(
        self,
        client: httpx.AsyncClient,
        query: str,
        time_range_minutes: int,
        limit: int,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        from_ts = now - timedelta(minutes=time_range_minutes)
        payload = {
            "filter": {
                "query": query,
                "from": from_ts.isoformat(),
                "to": now.isoformat(),
            },
            "sort": "-timestamp",
            "page": {"limit": min(limit, 1000)},
        }
        t0 = time.monotonic()
        try:
            resp = await client.post("/api/v2/logs/events/search", json=payload)
            resp.raise_for_status()
            data = resp.json()
            duration_ms = int((time.monotonic() - t0) * 1000)
            logs = []
            for event in data.get("data", []):
                attrs = event.get("attributes", {})
                logs.append({
                    "timestamp": attrs.get("timestamp", ""),
                    "message": attrs.get("message", ""),
                    "status": attrs.get("status", ""),
                    "service": attrs.get("service", ""),
                    "host": attrs.get("host", ""),
                    "tags": attrs.get("tags", []),
                })
            return {"success": True, "logs": logs, "total": len(logs), "duration_ms": duration_ms}
        except httpx.HTTPStatusError as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("[datadog] Async log search failed: %s", e.response.status_code)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}", "duration_ms": duration_ms}
        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("[datadog] Async log search error: %s", e)
            return {"success": False, "error": str(e), "duration_ms": duration_ms}

    async def _list_monitors(
        self,
        client: httpx.AsyncClient,
        query: str | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": 0, "page_size": 50}
        if query:
            params["query"] = query
        t0 = time.monotonic()
        try:
            resp = await client.get("/api/v1/monitor", params=params)
            resp.raise_for_status()
            monitors = resp.json()
            duration_ms = int((time.monotonic() - t0) * 1000)
            results = []
            for m in monitors if isinstance(monitors, list) else []:
                results.append({
                    "id": m.get("id"),
                    "name": m.get("name", ""),
                    "type": m.get("type", ""),
                    "query": m.get("query", ""),
                    "message": m.get("message", ""),
                    "overall_state": m.get("overall_state", ""),
                    "tags": m.get("tags", []),
                })
            return {"success": True, "monitors": results, "total": len(results), "duration_ms": duration_ms}
        except httpx.HTTPStatusError as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("[datadog] Async list monitors failed: %s", e.response.status_code)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}", "duration_ms": duration_ms}
        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("[datadog] Async list monitors error: %s", e)
            return {"success": False, "error": str(e), "duration_ms": duration_ms}

    async def _get_events(
        self,
        client: httpx.AsyncClient,
        query: str | None,
        time_range_minutes: int,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        from_ts = now - timedelta(minutes=time_range_minutes)
        payload: dict[str, Any] = {
            "filter": {
                "from": from_ts.isoformat(),
                "to": now.isoformat(),
            },
            "sort": "-timestamp",
            "page": {"limit": 50},
        }
        if query:
            payload["filter"]["query"] = query
        t0 = time.monotonic()
        try:
            resp = await client.post("/api/v2/events/search", json=payload)
            resp.raise_for_status()
            data = resp.json()
            duration_ms = int((time.monotonic() - t0) * 1000)
            events = []
            for event in data.get("data", []):
                attrs = event.get("attributes", {})
                events.append({
                    "timestamp": attrs.get("timestamp", ""),
                    "title": attrs.get("title", ""),
                    "message": attrs.get("message", attrs.get("text", "")),
                    "tags": attrs.get("tags", []),
                    "source": attrs.get("source", ""),
                })
            return {"success": True, "events": events, "total": len(events), "duration_ms": duration_ms}
        except httpx.HTTPStatusError as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("[datadog] Async events query failed: %s", e.response.status_code)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}", "duration_ms": duration_ms}
        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("[datadog] Async events query error: %s", e)
            return {"success": False, "error": str(e), "duration_ms": duration_ms}

    async def fetch_all(
        self,
        logs_query: str,
        time_range_minutes: int,
        logs_limit: int,
        monitor_query: str | None,
        events_query: str | None,
    ) -> dict[str, Any]:
        """Fetch logs, monitors, and events in parallel. Returns combined results with per-source timing."""
        async with httpx.AsyncClient(
            base_url=self.config.base_url,
            headers=self.config.headers,
            timeout=_DEFAULT_TIMEOUT,
        ) as client:
            logs_result, monitors_result, events_result = await asyncio.gather(
                self._search_logs(client, logs_query, time_range_minutes, logs_limit),
                self._list_monitors(client, monitor_query),
                self._get_events(client, events_query, time_range_minutes),
                return_exceptions=False,
            )

        return {
            "logs": logs_result,
            "monitors": monitors_result,
            "events": events_result,
        }
