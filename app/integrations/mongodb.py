"""Shared MongoDB integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for MongoDB instances.  All operations are production-safe: read-only,
timeouts enforced, result sizes capped.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

DEFAULT_MONGODB_AUTH_SOURCE = "admin"
DEFAULT_MONGODB_TIMEOUT_MS = 5000
DEFAULT_MONGODB_MAX_RESULTS = 50


class MongoDBConfig(StrictConfigModel):
    """Normalized MongoDB connection settings."""

    connection_string: str = ""
    database: str = ""
    auth_source: str = DEFAULT_MONGODB_AUTH_SOURCE
    tls: bool = True
    timeout_seconds: float = Field(default=10.0, gt=0)
    max_results: int = Field(default=DEFAULT_MONGODB_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("connection_string", mode="before")
    @classmethod
    def _normalize_connection_string(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("database", mode="before")
    @classmethod
    def _normalize_database(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("auth_source", mode="before")
    @classmethod
    def _normalize_auth_source(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_MONGODB_AUTH_SOURCE).strip()
        return normalized or DEFAULT_MONGODB_AUTH_SOURCE

    @property
    def is_configured(self) -> bool:
        return bool(self.connection_string)


@dataclass(frozen=True)
class MongoDBValidationResult:
    """Result of validating a MongoDB integration."""

    ok: bool
    detail: str


def build_mongodb_config(raw: dict[str, Any] | None) -> MongoDBConfig:
    """Build a normalized MongoDB config object from env/store data."""
    return MongoDBConfig.model_validate(raw or {})


def mongodb_config_from_env() -> MongoDBConfig | None:
    """Load a MongoDB config from env vars."""
    connection_string = os.getenv("MONGODB_CONNECTION_STRING", "").strip()
    if not connection_string:
        return None
    return build_mongodb_config(
        {
            "connection_string": connection_string,
            "database": os.getenv("MONGODB_DATABASE", "").strip(),
            "auth_source": os.getenv("MONGODB_AUTH_SOURCE", DEFAULT_MONGODB_AUTH_SOURCE).strip(),
            "tls": os.getenv("MONGODB_TLS", "true").strip().lower() in ("true", "1", "yes"),
        }
    )


def _get_client(config: MongoDBConfig) -> Any:
    """Create a pymongo MongoClient from config. Caller must close."""
    from pymongo import MongoClient

    return MongoClient(
        config.connection_string,
        authSource=config.auth_source,
        tls=config.tls,
        serverSelectionTimeoutMS=DEFAULT_MONGODB_TIMEOUT_MS,
        connectTimeoutMS=DEFAULT_MONGODB_TIMEOUT_MS,
        socketTimeoutMS=int(config.timeout_seconds * 1000),
        appName="opensre",
    )


def validate_mongodb_config(config: MongoDBConfig) -> MongoDBValidationResult:
    """Validate MongoDB connectivity with a lightweight ping command."""
    if not config.connection_string:
        return MongoDBValidationResult(ok=False, detail="MongoDB connection string is required.")

    try:
        client = _get_client(config)
        try:
            result = client.admin.command("ping")
            if result.get("ok") != 1:
                return MongoDBValidationResult(
                    ok=False, detail="MongoDB ping returned unexpected result."
                )
            # Get server info for version
            server_info = client.server_info()
            version = server_info.get("version", "unknown")
            db_name = config.database or "(default)"
            return MongoDBValidationResult(
                ok=True,
                detail=(f"Connected to MongoDB {version}; target database: {db_name}."),
            )
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return MongoDBValidationResult(ok=False, detail=f"MongoDB connection failed: {err}")


def get_server_status(config: MongoDBConfig) -> dict[str, Any]:
    """Retrieve server status (connections, opcounters, memory, uptime).

    Read-only: uses the ``serverStatus`` admin command.
    """
    if not config.is_configured:
        return {"source": "mongodb", "available": False, "error": "Not configured."}

    try:
        client = _get_client(config)
        try:
            status = client.admin.command("serverStatus")
            return {
                "source": "mongodb",
                "available": True,
                "version": status.get("version", ""),
                "uptime_seconds": status.get("uptimeMillis", 0) / 1000,
                "connections": {
                    "current": status.get("connections", {}).get("current", 0),
                    "available": status.get("connections", {}).get("available", 0),
                    "total_created": status.get("connections", {}).get("totalCreated", 0),
                },
                "opcounters": status.get("opcounters", {}),
                "memory": {
                    "resident_mb": status.get("mem", {}).get("resident", 0),
                    "virtual_mb": status.get("mem", {}).get("virtual", 0),
                },
                "storage_engine": status.get("storageEngine", {}).get("name", ""),
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mongodb", "available": False, "error": str(err)}


def get_current_ops(
    config: MongoDBConfig,
    threshold_ms: int = 1000,
) -> dict[str, Any]:
    """Retrieve currently running operations above a duration threshold.

    Read-only: uses ``currentOp`` admin command with ``microsecs_running`` filter for sub-second precision.
    Results are capped at ``config.max_results``.
    """
    if not config.is_configured:
        return {"source": "mongodb", "available": False, "error": "Not configured."}

    threshold_microsecs = max(0, threshold_ms * 1000)
    try:
        client = _get_client(config)
        try:
            result = client.admin.command(
                "currentOp", {"microsecs_running": {"$gte": threshold_microsecs}}
            )
            ops = result.get("inprog", [])
            # Cap results and strip potentially sensitive fields
            capped_ops = []
            for op in ops[: config.max_results]:
                capped_ops.append(
                    {
                        "opid": op.get("opid"),
                        "op": op.get("op"),
                        "ns": op.get("ns", ""),
                        "secs_running": op.get("secs_running", 0),
                        "microsecs_running": op.get("microsecs_running", 0),
                        "desc": op.get("desc", ""),
                        "wait_for_lock": op.get("waitingForLock", False),
                        "plan_summary": op.get("planSummary", ""),
                    }
                )
            return {
                "source": "mongodb",
                "available": True,
                "threshold_ms": threshold_ms,
                "total_ops": len(ops),
                "returned_ops": len(capped_ops),
                "operations": capped_ops,
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mongodb", "available": False, "error": str(err)}


def get_rs_status(config: MongoDBConfig) -> dict[str, Any]:
    """Retrieve replica set status (member states, oplog lag).

    Read-only: uses ``replSetGetStatus`` admin command.
    Returns empty members list if the server is not part of a replica set.
    """
    if not config.is_configured:
        return {"source": "mongodb", "available": False, "error": "Not configured."}

    try:
        client = _get_client(config)
        try:
            rs = client.admin.command("replSetGetStatus")
            members = []
            for member in rs.get("members", []):
                members.append(
                    {
                        "name": member.get("name", ""),
                        "state": member.get("stateStr", ""),
                        "health": member.get("health", 0),
                        "uptime_seconds": member.get("uptime", 0),
                        "optime": str(member.get("optimeDate", "")),
                        "last_heartbeat": str(member.get("lastHeartbeat", "")),
                        "ping_ms": member.get("pingMs", None),
                    }
                )
            return {
                "source": "mongodb",
                "available": True,
                "set_name": rs.get("set", ""),
                "my_state": rs.get("myState", 0),
                "heartbeat_interval_ms": rs.get("heartbeatIntervalMillis", 0),
                "members": members,
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        error_str = str(err)
        # Not a replica set is not an error per se
        if "not running with --replSet" in error_str or "NotYetInitialized" in error_str:
            return {
                "source": "mongodb",
                "available": True,
                "set_name": "",
                "members": [],
                "note": "Server is not part of a replica set.",
            }
        return {"source": "mongodb", "available": False, "error": error_str}


def get_profiler_data(
    config: MongoDBConfig,
    threshold_ms: int = 100,
    limit: int | None = None,
) -> dict[str, Any]:
    """Retrieve slow query data from the system.profile collection.

    Read-only: reads ``system.profile``.  Returns empty results when profiling
    is not enabled (level 0).  Results capped at ``config.max_results``.
    """
    if not config.is_configured:
        return {"source": "mongodb", "available": False, "error": "Not configured."}
    if not config.database:
        return {
            "source": "mongodb",
            "available": False,
            "error": "Database name is required for profiler data.",
        }

    effective_limit = min(limit or config.max_results, config.max_results)
    try:
        client = _get_client(config)
        try:
            db = client[config.database]
            # Check profiling level
            profile_status = db.command("profile", -1)
            profiling_level = profile_status.get("was", 0)

            if profiling_level == 0:
                return {
                    "source": "mongodb",
                    "available": True,
                    "profiling_level": 0,
                    "note": (
                        "Profiling is disabled on this database. "
                        "Enable it with db.setProfilingLevel(1) for slow queries "
                        "or db.setProfilingLevel(2) for all queries."
                    ),
                    "entries": [],
                }

            cursor = (
                db["system.profile"]
                .find({"millis": {"$gte": threshold_ms}})
                .sort("ts", -1)
                .limit(effective_limit)
            )
            entries = []
            for doc in cursor:
                entries.append(
                    {
                        "op": doc.get("op", ""),
                        "ns": doc.get("ns", ""),
                        "millis": doc.get("millis", 0),
                        "ts": str(doc.get("ts", "")),
                        "plan_summary": doc.get("planSummary", ""),
                        "docs_examined": doc.get("docsExamined", 0),
                        "keys_examined": doc.get("keysExamined", 0),
                        "n_returned": doc.get("nreturned", 0),
                        "response_length": doc.get("responseLength", 0),
                    }
                )
            return {
                "source": "mongodb",
                "available": True,
                "profiling_level": profiling_level,
                "threshold_ms": threshold_ms,
                "total_entries": len(entries),
                "entries": entries,
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mongodb", "available": False, "error": str(err)}


def get_collection_stats(
    config: MongoDBConfig,
    collection: str,
) -> dict[str, Any]:
    """Retrieve statistics for a specific collection.

    Read-only: uses the ``collStats`` command.
    """
    if not config.is_configured:
        return {"source": "mongodb", "available": False, "error": "Not configured."}
    if not config.database:
        return {
            "source": "mongodb",
            "available": False,
            "error": "Database name is required for collection stats.",
        }
    if not collection:
        return {
            "source": "mongodb",
            "available": False,
            "error": "Collection name is required.",
        }

    try:
        client = _get_client(config)
        try:
            db = client[config.database]
            stats = db.command("collStats", collection)
            return {
                "source": "mongodb",
                "available": True,
                "ns": stats.get("ns", ""),
                "count": stats.get("count", 0),
                "size_bytes": stats.get("size", 0),
                "avg_obj_size_bytes": stats.get("avgObjSize", 0),
                "storage_size_bytes": stats.get("storageSize", 0),
                "total_index_size_bytes": stats.get("totalIndexSize", 0),
                "index_count": stats.get("nindexes", 0),
                "capped": stats.get("capped", False),
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mongodb", "available": False, "error": str(err)}


def get_db_stats(config: MongoDBConfig) -> dict[str, Any]:
    """Retrieve database-level statistics.

    Read-only: uses the ``dbStats`` command.
    """
    if not config.is_configured:
        return {"source": "mongodb", "available": False, "error": "Not configured."}
    if not config.database:
        return {
            "source": "mongodb",
            "available": False,
            "error": "Database name is required.",
        }

    try:
        client = _get_client(config)
        try:
            db = client[config.database]
            stats = db.command("dbStats")
            return {
                "source": "mongodb",
                "available": True,
                "db": stats.get("db", ""),
                "collections": stats.get("collections", 0),
                "objects": stats.get("objects", 0),
                "data_size_bytes": stats.get("dataSize", 0),
                "storage_size_bytes": stats.get("storageSize", 0),
                "indexes": stats.get("indexes", 0),
                "index_size_bytes": stats.get("indexSize", 0),
            }
        finally:
            client.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "mongodb", "available": False, "error": str(err)}
