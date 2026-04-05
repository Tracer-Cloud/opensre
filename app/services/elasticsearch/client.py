"""Elasticsearch REST API client.

Uses the ES HTTP API directly via httpx (no elasticsearch-py SDK).
Supports clusters with security disabled (no credentials) and
clusters requiring API key authentication.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30


@dataclass
class ElasticsearchConfig:
    url: str
    api_key: str | None = None
    index_pattern: str = field(default="*")

    @property
    def base_url(self) -> str:
        return self.url.rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"ApiKey {self.api_key}"
        return h


class ElasticsearchClient:
    """Synchronous client for querying Elasticsearch via the REST API."""

    def __init__(self, config: ElasticsearchConfig) -> None:
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
        return bool(self.config.url)

    def check_security(self) -> dict[str, Any]:
        """Probe the cluster to detect whether security (authentication) is enabled.

        Makes an unauthenticated GET /_cluster/health request.
        - HTTP 200  → security disabled (no credentials required)
        - HTTP 401  → security enabled  (credentials required)
        - anything else → error
        """
        try:
            resp = httpx.get(
                f"{self.config.base_url}/_cluster/health",
                timeout=_DEFAULT_TIMEOUT,
            )
            if resp.status_code == 200:
                security_enabled = False
            elif resp.status_code == 401:
                security_enabled = True
            else:
                return {"success": False, "error": f"Unexpected status {resp.status_code} from /_cluster/health"}
            return {"success": True, "security_enabled": security_enabled}
        except Exception as e:
            logger.warning(
                "[elasticsearch] check_security error type=%s detail=%s",
                type(e).__name__,
                e,
            )
            return {"success": False, "error": str(e)}

    def list_indices(self) -> dict[str, Any]:
        """List all indices via GET /_cat/indices?format=json."""
        try:
            resp = self._get_client().get("/_cat/indices", params={"format": "json"})
            resp.raise_for_status()
            raw: list[dict[str, Any]] = resp.json()
            indices = [
                {
                    "index": idx.get("index", ""),
                    "health": idx.get("health", ""),
                    "status": idx.get("status", ""),
                    "docs_count": idx.get("docs.count", ""),
                    "store_size": idx.get("store.size", ""),
                }
                for idx in raw
            ]
            return {"success": True, "indices": indices, "total": len(indices)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[elasticsearch] list_indices HTTP failure status=%s",
                e.response.status_code,
            )
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[elasticsearch] list_indices error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def list_data_streams(self) -> dict[str, Any]:
        """List all data streams via GET /_data_stream."""
        try:
            resp = self._get_client().get("/_data_stream")
            resp.raise_for_status()
            data = resp.json()
            streams = data.get("data_streams", [])
            results = [
                {
                    "name": s.get("name", ""),
                    "status": s.get("status", ""),
                    "indices": [i.get("index_name", "") for i in s.get("indices", [])],
                }
                for s in streams
            ]
            return {"success": True, "data_streams": results, "total": len(results)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[elasticsearch] list_data_streams HTTP failure status=%s",
                e.response.status_code,
            )
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[elasticsearch] list_data_streams error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def search_logs(
        self,
        query: str = "*",
        time_range_minutes: int = 60,
        limit: int = 50,
        index_pattern: str | None = None,
        timestamp_field: str = "@timestamp",
    ) -> dict[str, Any]:
        """Search logs via POST /{index_pattern}/_search with a time-range filter.

        Args:
            query: Lucene/KQL query string (default "*" = all documents)
            time_range_minutes: How far back to search (default 60 minutes)
            limit: Maximum number of hits to return (capped at 1000)
            index_pattern: Override config index_pattern for this call
            timestamp_field: Timestamp field for range filtering (default "@timestamp")
        """
        pattern = index_pattern or self.config.index_pattern
        now = datetime.now(UTC)
        from_ts = now - timedelta(minutes=time_range_minutes)

        payload: dict[str, Any] = {
            "size": min(limit, 1000),
            "sort": [{timestamp_field: {"order": "desc"}}],
            "query": {
                "bool": {
                    "must": [
                        {"query_string": {"query": query, "default_field": "*"}},
                        {"range": {timestamp_field: {"gte": from_ts.isoformat(), "lte": now.isoformat()}}},
                    ]
                }
            },
        }

        try:
            resp = self._get_client().post(f"/{pattern}/_search", json=payload)
            resp.raise_for_status()
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            logs = [
                {
                    "timestamp": src.get(timestamp_field, ""),
                    "message": src.get("message", ""),
                    "level": src.get("level", src.get("log.level", "")),
                    "service": src.get("service", src.get("service.name", "")),
                    "index": hit.get("_index", ""),
                    **{k: v for k, v in src.items() if k not in {timestamp_field, "message", "level", "service"}},
                }
                for hit in hits
                for src in [hit.get("_source", {})]
            ]
            return {"success": True, "logs": logs, "total": len(logs), "query": query}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[elasticsearch] search_logs HTTP failure status=%s query=%r window=%sm",
                e.response.status_code,
                query,
                time_range_minutes,
            )
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[elasticsearch] search_logs error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def get_cluster_health(self) -> dict[str, Any]:
        """GET /_cluster/health — returns cluster name, status, and shard counts."""
        try:
            resp = self._get_client().get("/_cluster/health")
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return {
                "success": True,
                "cluster_name": data.get("cluster_name", ""),
                "status": data.get("status", ""),
                "number_of_nodes": data.get("number_of_nodes", 0),
                "number_of_data_nodes": data.get("number_of_data_nodes", 0),
                "active_primary_shards": data.get("active_primary_shards", 0),
                "active_shards": data.get("active_shards", 0),
                "unassigned_shards": data.get("unassigned_shards", 0),
            }
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[elasticsearch] get_cluster_health HTTP failure status=%s",
                e.response.status_code,
            )
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[elasticsearch] get_cluster_health error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}
