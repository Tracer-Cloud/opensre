"""Shared MongoDB Atlas integration helpers.

Provides configuration, connectivity validation, and read-only API queries
against the MongoDB Atlas Admin API v2.  All operations are production-safe:
read-only, timeouts enforced, result sizes capped.

Atlas API reference: https://www.mongodb.com/docs/atlas/reference/api-resources-spec/v2/
Authentication: HTTP Digest with API public/private key pair.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

DEFAULT_ATLAS_BASE_URL = "https://cloud.mongodb.com/api/atlas/v2"
DEFAULT_ATLAS_TIMEOUT = 15
DEFAULT_ATLAS_MAX_RESULTS = 50


class MongoDBAtlasConfig(StrictConfigModel):
    """Normalized MongoDB Atlas API connection settings."""

    api_public_key: str = ""
    api_private_key: str = ""
    project_id: str = ""
    base_url: str = DEFAULT_ATLAS_BASE_URL
    timeout_seconds: float = Field(default=float(DEFAULT_ATLAS_TIMEOUT), gt=0)
    max_results: int = Field(default=DEFAULT_ATLAS_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("api_public_key", mode="before")
    @classmethod
    def _normalize_public_key(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("api_private_key", mode="before")
    @classmethod
    def _normalize_private_key(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("project_id", mode="before")
    @classmethod
    def _normalize_project_id(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_ATLAS_BASE_URL).strip().rstrip("/")
        return normalized or DEFAULT_ATLAS_BASE_URL

    @property
    def is_configured(self) -> bool:
        return bool(self.api_public_key and self.api_private_key and self.project_id)


@dataclass(frozen=True)
class MongoDBAtlasValidationResult:
    """Result of validating a MongoDB Atlas integration."""

    ok: bool
    detail: str


def build_mongodb_atlas_config(raw: dict[str, Any] | None) -> MongoDBAtlasConfig:
    """Build a normalized MongoDB Atlas config object from env/store data."""
    return MongoDBAtlasConfig.model_validate(raw or {})


def mongodb_atlas_config_from_env() -> MongoDBAtlasConfig | None:
    """Load a MongoDB Atlas config from env vars."""
    public_key = os.getenv("MONGODB_ATLAS_PUBLIC_KEY", "").strip()
    private_key = os.getenv("MONGODB_ATLAS_PRIVATE_KEY", "").strip()
    project_id = os.getenv("MONGODB_ATLAS_PROJECT_ID", "").strip()
    if not public_key or not private_key or not project_id:
        return None
    return build_mongodb_atlas_config({
        "api_public_key": public_key,
        "api_private_key": private_key,
        "project_id": project_id,
        "base_url": os.getenv("MONGODB_ATLAS_BASE_URL", DEFAULT_ATLAS_BASE_URL).strip(),
    })


def _get_client(config: MongoDBAtlasConfig) -> httpx.Client:
    """Create an httpx client with Atlas Digest auth. Caller must close."""
    return httpx.Client(
        base_url=config.base_url,
        auth=httpx.DigestAuth(config.api_public_key, config.api_private_key),
        headers={
            "Accept": "application/vnd.atlas.2025-03-12+json",
            "Content-Type": "application/json",
        },
        timeout=config.timeout_seconds,
    )


def validate_mongodb_atlas_config(
    config: MongoDBAtlasConfig,
) -> MongoDBAtlasValidationResult:
    """Validate Atlas connectivity by listing project clusters."""
    if not config.is_configured:
        return MongoDBAtlasValidationResult(
            ok=False,
            detail="MongoDB Atlas API public key, private key, and project ID are required.",
        )

    try:
        client = _get_client(config)
        try:
            resp = client.get(f"/groups/{config.project_id}/clusters", params={"itemsPerPage": 1})
            resp.raise_for_status()
            data = resp.json()
            total = data.get("totalCount", 0)
            return MongoDBAtlasValidationResult(
                ok=True,
                detail=f"Connected to Atlas project {config.project_id}; {total} cluster(s) found.",
            )
        finally:
            client.close()
    except httpx.HTTPStatusError as err:
        return MongoDBAtlasValidationResult(
            ok=False,
            detail=f"Atlas API returned {err.response.status_code}: {err.response.text[:200]}",
        )
    except Exception as err:  # noqa: BLE001
        return MongoDBAtlasValidationResult(
            ok=False, detail=f"Atlas connection failed: {err}"
        )


def get_clusters(config: MongoDBAtlasConfig) -> dict[str, Any]:
    """Retrieve all clusters in the Atlas project.

    Read-only: GET /groups/{projectId}/clusters
    """
    if not config.is_configured:
        return {"source": "mongodb_atlas", "available": False, "error": "Not configured."}

    try:
        client = _get_client(config)
        try:
            resp = client.get(
                f"/groups/{config.project_id}/clusters",
                params={"itemsPerPage": config.max_results},
            )
            resp.raise_for_status()
            data = resp.json()
            clusters = []
            for c in data.get("results", []):
                clusters.append({
                    "name": c.get("name", ""),
                    "state": c.get("stateName", ""),
                    "cluster_type": c.get("clusterType", ""),
                    "mongo_db_version": c.get("mongoDBVersion", ""),
                    "connection_strings": {
                        "standard": c.get("connectionStrings", {}).get("standard", ""),
                        "standard_srv": c.get("connectionStrings", {}).get("standardSrv", ""),
                    },
                    "paused": c.get("paused", False),
                    "disk_size_gb": c.get("diskSizeGB"),
                    "replication_specs": [
                        {
                            "zone_name": rs.get("zoneName", ""),
                            "num_shards": rs.get("numShards", 1),
                            "region_configs": [
                                {
                                    "provider": rc.get("providerName", ""),
                                    "region": rc.get("regionName", ""),
                                    "priority": rc.get("priority"),
                                    "electable_nodes": rc.get("electableSpecs", {}).get("nodeCount", 0),
                                    "instance_size": rc.get("electableSpecs", {}).get("instanceSize", ""),
                                }
                                for rc in rs.get("regionConfigs", [])
                            ],
                        }
                        for rs in c.get("replicationSpecs", [])
                    ],
                })
            return {
                "source": "mongodb_atlas",
                "available": True,
                "total_clusters": data.get("totalCount", 0),
                "clusters": clusters,
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mongodb_atlas", "available": False, "error": str(err)}


def get_alerts(
    config: MongoDBAtlasConfig,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Retrieve open alerts for the Atlas project.

    Read-only: GET /groups/{projectId}/alerts
    """
    if not config.is_configured:
        return {"source": "mongodb_atlas", "available": False, "error": "Not configured."}

    effective_limit = min(max_results or config.max_results, config.max_results)
    try:
        client = _get_client(config)
        try:
            resp = client.get(
                f"/groups/{config.project_id}/alerts",
                params={"itemsPerPage": effective_limit, "status": "OPEN"},
            )
            resp.raise_for_status()
            data = resp.json()
            alerts = []
            for a in data.get("results", []):
                alerts.append({
                    "id": a.get("id", ""),
                    "event_type": a.get("eventTypeName", ""),
                    "status": a.get("status", ""),
                    "created": a.get("created", ""),
                    "updated": a.get("updated", ""),
                    "cluster_name": a.get("clusterName", ""),
                    "replica_set_name": a.get("replicaSetName", ""),
                    "metric_name": a.get("metricName", ""),
                    "current_value": a.get("currentValue", {}),
                })
            return {
                "source": "mongodb_atlas",
                "available": True,
                "total_alerts": data.get("totalCount", 0),
                "alerts": alerts,
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mongodb_atlas", "available": False, "error": str(err)}


def _resolve_primary_process(
    client: httpx.Client,
    config: MongoDBAtlasConfig,
    cluster_name: str,
) -> dict[str, Any] | None:
    """Find the primary process for a cluster. Returns None if not found."""
    resp = client.get(
        f"/groups/{config.project_id}/processes",
        params={"itemsPerPage": 500},
    )
    resp.raise_for_status()
    processes = resp.json().get("results", [])

    target: dict[str, Any] | None = None
    for p in processes:
        hostname = p.get("hostname", "")
        if hostname.lower().startswith(cluster_name.lower() + "-") or hostname.lower() == cluster_name.lower():
            if p.get("typeName") == "REPLICA_PRIMARY":
                target = p
                break
            if target is None:
                target = p
    return target


def atlas_is_available(sources: dict[str, dict]) -> bool:
    """Check if MongoDB Atlas integration credentials are present."""
    atlas = sources.get("mongodb_atlas", {})
    return bool(
        atlas.get("api_public_key")
        and atlas.get("api_private_key")
        and atlas.get("project_id")
    )


def atlas_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract MongoDB Atlas credentials from resolved integrations."""
    atlas = sources.get("mongodb_atlas", {})
    return {
        "api_public_key": atlas.get("api_public_key", ""),
        "api_private_key": atlas.get("api_private_key", ""),
        "project_id": atlas.get("project_id", ""),
        "base_url": atlas.get("base_url", DEFAULT_ATLAS_BASE_URL),
    }


def get_cluster_metrics(
    config: MongoDBAtlasConfig,
    cluster_name: str,
    granularity: str = "PT1H",
    period: str = "P1D",
) -> dict[str, Any]:
    """Retrieve process-level metrics for a cluster.

    Read-only: GET /groups/{projectId}/processes/{processId}/measurements
    First resolves processes for the cluster, then fetches metrics for the primary.
    """
    if not config.is_configured:
        return {"source": "mongodb_atlas", "available": False, "error": "Not configured."}
    if not cluster_name:
        return {"source": "mongodb_atlas", "available": False, "error": "cluster_name is required."}

    try:
        client = _get_client(config)
        try:
            target_process = _resolve_primary_process(client, config, cluster_name)

            if not target_process:
                return {
                    "source": "mongodb_atlas",
                    "available": True,
                    "note": f"No processes found for cluster '{cluster_name}'.",
                    "measurements": {},
                }

            process_id = f"{target_process['hostname']}:{target_process['port']}"

            # Fetch key metrics
            metric_names = [
                "CONNECTIONS",
                "OPCOUNTER_CMD",
                "OPCOUNTER_QUERY",
                "OPCOUNTER_INSERT",
                "OPCOUNTER_UPDATE",
                "OPCOUNTER_DELETE",
                "QUERY_TARGETING_SCANNED_OBJECTS_PER_RETURNED",
                "SYSTEM_CPU_USER",
                "SYSTEM_MEMORY_USED",
                "CACHE_USED_BYTES",
                "DISK_PARTITION_IOPS_READ",
                "DISK_PARTITION_IOPS_WRITE",
            ]

            resp = client.get(
                f"/groups/{config.project_id}/processes/{process_id}/measurements",
                params={
                    "granularity": granularity,
                    "period": period,
                    "m": metric_names,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            measurements = {}
            for m in data.get("measurements", []):
                name = m.get("name", "")
                data_points = m.get("dataPoints", [])
                # Get the most recent non-null value
                latest = None
                for dp in reversed(data_points):
                    if dp.get("value") is not None:
                        latest = dp
                        break
                if latest:
                    measurements[name] = {
                        "value": latest.get("value"),
                        "units": m.get("units", ""),
                        "timestamp": latest.get("timestamp", ""),
                    }

            return {
                "source": "mongodb_atlas",
                "available": True,
                "process_id": process_id,
                "process_type": target_process.get("typeName", ""),
                "mongo_version": target_process.get("version", ""),
                "measurements": measurements,
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mongodb_atlas", "available": False, "error": str(err)}


def get_performance_advisor(
    config: MongoDBAtlasConfig,
    cluster_name: str,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Retrieve Performance Advisor slow query and index suggestions.

    Read-only: GET /groups/{projectId}/processes/{processId}/performanceAdvisor/suggestedIndexes
    """
    if not config.is_configured:
        return {"source": "mongodb_atlas", "available": False, "error": "Not configured."}
    if not cluster_name:
        return {"source": "mongodb_atlas", "available": False, "error": "cluster_name is required."}

    effective_limit = min(max_results or config.max_results, config.max_results)
    try:
        client = _get_client(config)
        try:
            target_process = _resolve_primary_process(client, config, cluster_name)

            if not target_process:
                return {
                    "source": "mongodb_atlas",
                    "available": True,
                    "note": f"No processes found for cluster '{cluster_name}'.",
                    "suggested_indexes": [],
                    "slow_queries": [],
                }

            process_id = f"{target_process['hostname']}:{target_process['port']}"

            # Get suggested indexes
            resp = client.get(
                f"/groups/{config.project_id}/processes/{process_id}/performanceAdvisor/suggestedIndexes",
                params={"nIndexes": effective_limit},
            )
            resp.raise_for_status()
            index_data = resp.json()

            suggested_indexes = []
            for idx in index_data.get("suggestedIndexes", []):
                suggested_indexes.append({
                    "namespace": idx.get("namespace", ""),
                    "index": idx.get("index", []),
                    "weight": idx.get("weight", 0),
                    "impact": idx.get("impact", []),
                })

            # Get slow queries
            resp = client.get(
                f"/groups/{config.project_id}/processes/{process_id}/performanceAdvisor/slowQueryLogs",
                params={"nLogs": effective_limit},
            )
            resp.raise_for_status()
            slow_data = resp.json()

            slow_queries = []
            for sq in slow_data.get("slowQueries", []):
                slow_queries.append({
                    "namespace": sq.get("namespace", ""),
                    "line": sq.get("line", "")[:200],
                    "millis": sq.get("millis", 0),
                })

            return {
                "source": "mongodb_atlas",
                "available": True,
                "process_id": process_id,
                "total_suggested_indexes": len(suggested_indexes),
                "suggested_indexes": suggested_indexes,
                "total_slow_queries": len(slow_queries),
                "slow_queries": slow_queries,
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mongodb_atlas", "available": False, "error": str(err)}


def get_cluster_events(
    config: MongoDBAtlasConfig,
    cluster_name: str,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Retrieve recent events for the Atlas project filtered by cluster.

    Read-only: GET /groups/{projectId}/events
    """
    if not config.is_configured:
        return {"source": "mongodb_atlas", "available": False, "error": "Not configured."}

    effective_limit = min(max_results or config.max_results, config.max_results)
    try:
        client = _get_client(config)
        try:
            params: dict[str, Any] = {"itemsPerPage": effective_limit}
            if cluster_name:
                params["clusterName"] = cluster_name

            resp = client.get(
                f"/groups/{config.project_id}/events",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            events = []
            for e in data.get("results", []):
                events.append({
                    "id": e.get("id", ""),
                    "event_type": e.get("eventTypeName", ""),
                    "created": e.get("created", ""),
                    "cluster_name": e.get("clusterName", ""),
                    "replica_set_name": e.get("replicaSetName", ""),
                    "is_global_admin": e.get("isGlobalAdmin", False),
                    "target_username": e.get("targetUsername", ""),
                })
            return {
                "source": "mongodb_atlas",
                "available": True,
                "total_events": data.get("totalCount", 0),
                "events": events,
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mongodb_atlas", "available": False, "error": str(err)}
