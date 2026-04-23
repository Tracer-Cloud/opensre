"""Datadog API client for querying logs, events, and monitors.

Uses the Datadog REST API directly via httpx (no SDK dependency).
Credentials come from the user's Datadog integration stored in the Tracer web app DB.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import field_validator

from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30


class DatadogConfig(StrictConfigModel):
    api_key: str
    app_key: str
    site: str = "datadoghq.com"

    @field_validator("site", mode="before")
    @classmethod
    def _normalize_site(cls, value: object) -> str:
        return str(value or "datadoghq.com").strip() or "datadoghq.com"

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
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                headers=self.config.headers,
                timeout=_DEFAULT_TIMEOUT,
            )
        return self._client

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
            resp = self._get_client().post("/api/v2/logs/events/search", json=payload)
            resp.raise_for_status()
            data = resp.json()

            logs = []
            for event in data.get("data", []):
                attrs = event.get("attributes", {})
                custom = attrs.get("attributes", {}) or {}
                log = {
                    "timestamp": attrs.get("timestamp", ""),
                    "message": attrs.get("message", ""),
                    "status": attrs.get("status", ""),
                    "service": attrs.get("service", ""),
                    "host": attrs.get("host", ""),
                    "tags": attrs.get("tags", []),
                }
                # Merge custom JSON attributes so pod/node fields are top-level
                log.update({k: v for k, v in custom.items() if isinstance(k, str)})
                logs.append(log)

            return {"success": True, "logs": logs, "total": len(logs)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[datadog] Log search HTTP failure status=%s query=%r window=%sm "
                "(check API/app key permissions and query syntax)",
                e.response.status_code,
                query,
                time_range_minutes,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning(
                "[datadog] Log search request error type=%s detail=%s "
                "(network/auth/timeout likely)",
                type(e).__name__,
                e,
            )
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
            resp = self._get_client().get("/api/v1/monitor", params=params)
            resp.raise_for_status()
            monitors = resp.json()

            results = []
            for m in monitors if isinstance(monitors, list) else []:
                results.append(
                    {
                        "id": m.get("id"),
                        "name": m.get("name", ""),
                        "type": m.get("type", ""),
                        "query": m.get("query", ""),
                        "message": m.get("message", ""),
                        "overall_state": m.get("overall_state", ""),
                        "tags": m.get("tags", []),
                    }
                )

            return {"success": True, "monitors": results, "total": len(results)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[datadog] List monitors HTTP failure status=%s query=%r "
                "(verify monitor.read scope and org/site)",
                e.response.status_code,
                query,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning(
                "[datadog] List monitors request error type=%s detail=%s",
                type(e).__name__,
                e,
            )
            return {"success": False, "error": str(e)}

    def get_pods_on_node(
        self,
        node_ip: str,
        time_range_minutes: int = 60,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Query Datadog Infrastructure API for all pods running on a given node IP.

        Uses the Datadog log search API to find pod telemetry tagged with the node IP,
        then returns the unique set of pods with their last-seen status.

        Args:
            node_ip: The node's IP address (from an alert, e.g. "10.0.1.42")
            time_range_minutes: How far back to look for pod activity
            limit: Max log events to scan for pod tags

        Returns:
            dict with pods list, each entry containing pod_name, namespace, container,
            node_ip, node_name, status/exit_code if available.
        """
        query = f"host:{node_ip} OR node_ip:{node_ip}"
        result = self.search_logs(query, time_range_minutes=time_range_minutes, limit=limit)

        if not result.get("success"):
            return {"success": False, "error": result.get("error", "Unknown error"), "pods": []}

        seen: set[str] = set()
        pods: list[dict[str, Any]] = []
        for log in result.get("logs", []):
            pod_name = container_name = kube_namespace = exit_code = node_name = found_node_ip = (
                None
            )
            for tag in log.get("tags", []):
                if not isinstance(tag, str) or ":" not in tag:
                    continue
                k, _, v = tag.partition(":")
                if k == "pod_name":
                    pod_name = v
                elif k == "container_name":
                    container_name = v
                elif k == "kube_namespace":
                    kube_namespace = v
                elif k == "exit_code":
                    exit_code = v
                elif k == "node_name":
                    node_name = v
                elif k == "node_ip":
                    found_node_ip = v

            pod_name = pod_name or log.get("pod_name")
            container_name = container_name or log.get("container_name")
            kube_namespace = kube_namespace or log.get("kube_namespace")
            node_name = node_name or log.get("node_name")
            found_node_ip = found_node_ip or log.get("node_ip", node_ip)
            if exit_code is None and log.get("exit_code") is not None:
                exit_code = str(log["exit_code"])

            if pod_name and pod_name not in seen:
                seen.add(pod_name)
                pods.append(
                    {
                        "pod_name": pod_name,
                        "namespace": kube_namespace,
                        "container": container_name,
                        "node_ip": found_node_ip or node_ip,
                        "node_name": node_name,
                        "exit_code": exit_code,
                        "status": "failed" if exit_code and exit_code != "0" else "running",
                    }
                )

        return {"success": True, "pods": pods, "total": len(pods), "node_ip": node_ip}

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
            resp = self._get_client().post("/api/v2/events/search", json=payload)
            resp.raise_for_status()
            data = resp.json()

            events = []
            for event in data.get("data", []):
                attrs = event.get("attributes", {})
                events.append(
                    {
                        "timestamp": attrs.get("timestamp", ""),
                        "title": attrs.get("title", ""),
                        "message": attrs.get("message", attrs.get("text", "")),
                        "tags": attrs.get("tags", []),
                        "source": attrs.get("source", ""),
                    }
                )

            return {"success": True, "events": events, "total": len(events)}
        except httpx.HTTPStatusError as e:
            logger.warning("[datadog] Events query failed: %s", e.response.status_code)
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
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
                custom = attrs.get("attributes", {}) or {}
                log = {
                    "timestamp": attrs.get("timestamp", ""),
                    "message": attrs.get("message", ""),
                    "status": attrs.get("status", ""),
                    "service": attrs.get("service", ""),
                    "host": attrs.get("host", ""),
                    "tags": attrs.get("tags", []),
                }
                log.update({k: v for k, v in custom.items() if isinstance(k, str)})
                logs.append(log)
            return {"success": True, "logs": logs, "total": len(logs), "duration_ms": duration_ms}
        except httpx.HTTPStatusError as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("[datadog] Async log search failed: %s", e.response.status_code)
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                "duration_ms": duration_ms,
            }
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
                results.append(
                    {
                        "id": m.get("id"),
                        "name": m.get("name", ""),
                        "type": m.get("type", ""),
                        "query": m.get("query", ""),
                        "message": m.get("message", ""),
                        "overall_state": m.get("overall_state", ""),
                        "tags": m.get("tags", []),
                    }
                )
            return {
                "success": True,
                "monitors": results,
                "total": len(results),
                "duration_ms": duration_ms,
            }
        except httpx.HTTPStatusError as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("[datadog] Async list monitors failed: %s", e.response.status_code)
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                "duration_ms": duration_ms,
            }
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
                events.append(
                    {
                        "timestamp": attrs.get("timestamp", ""),
                        "title": attrs.get("title", ""),
                        "message": attrs.get("message", attrs.get("text", "")),
                        "tags": attrs.get("tags", []),
                        "source": attrs.get("source", ""),
                    }
                )
            return {
                "success": True,
                "events": events,
                "total": len(events),
                "duration_ms": duration_ms,
            }
        except httpx.HTTPStatusError as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("[datadog] Async events query failed: %s", e.response.status_code)
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                "duration_ms": duration_ms,
            }
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
