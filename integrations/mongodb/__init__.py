"""Shared MongoDB integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for MongoDB instances.  All operations are production-safe: read-only,
timeouts enforced, result sizes capped.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from config.constants.mongodb import (
    MONGODB_AUTH_SOURCE_ENV,
    MONGODB_CONNECTION_STRING_ENV,
    MONGODB_DATABASE_ENV,
    MONGODB_TLS_ENV,
)
from config.llm_credentials import resolve_env_credential
from config.strict_config import StrictConfigModel
from core.tool_framework.utils import tool_unavailable
from integrations._validation_helpers import report_classify_failure, report_validation_failure

logger = logging.getLogger(__name__)

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
    connection_string = resolve_env_credential(MONGODB_CONNECTION_STRING_ENV)
    if not connection_string:
        return None
    return build_mongodb_config(
        {
            "connection_string": connection_string,
            "database": os.getenv(MONGODB_DATABASE_ENV, "").strip(),
            "auth_source": os.getenv(MONGODB_AUTH_SOURCE_ENV, DEFAULT_MONGODB_AUTH_SOURCE).strip(),
            "tls": os.getenv(MONGODB_TLS_ENV, "true").strip().lower() in ("true", "1", "yes"),
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
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="mongodb",
            method="validate_mongodb_config",
        )
        return MongoDBValidationResult(ok=False, detail=f"MongoDB connection failed: {err}")


def mongodb_is_available(sources: dict[str, dict]) -> bool:
    """Check if MongoDB integration params are present in available sources."""
    return bool(sources.get("mongodb", {}).get("connection_string"))


def mongodb_database_is_available(sources: dict[str, dict]) -> bool:
    """Check if MongoDB integration params including a database name are present.

    Required for tools that operate on a specific database (profiler, collection stats).
    """
    mg = sources.get("mongodb", {})
    return bool(mg.get("connection_string") and mg.get("database"))


def mongodb_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract MongoDB connection params from resolved integrations.

    Credentials are resolved from the integration store or environment, so the
    LLM never needs to supply connection_string directly.
    """
    mg = sources.get("mongodb", {})
    return {
        "connection_string": str(mg.get("connection_string", "")).strip(),
        "database": str(mg.get("database", "")).strip(),
        "auth_source": str(mg.get("auth_source", DEFAULT_MONGODB_AUTH_SOURCE)).strip(),
        "tls": bool(mg.get("tls", True)),
    }


def get_server_status(config: MongoDBConfig) -> dict[str, Any]:
    """Retrieve server status (connections, opcounters, memory, uptime).

    Read-only: uses the ``serverStatus`` admin command.
    """
    if not config.is_configured:
        return tool_unavailable("mongodb", "Not configured.")

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
    except Exception as err:
        if getattr(err, "code", None) == 13:
            return tool_unavailable(
                "mongodb",
                "MongoDB user lacks admin privileges for serverStatus. Grant the 'clusterMonitor' role.",
            )
        report_validation_failure(
            err,
            logger=logger,
            integration="mongodb",
            method="get_server_status",
        )
        return tool_unavailable("mongodb", str(err))


def get_current_ops(
    config: MongoDBConfig,
    threshold_ms: int = 1000,
) -> dict[str, Any]:
    """Retrieve currently running operations above a duration threshold.

    Read-only: uses ``currentOp`` admin command with ``microsecs_running`` filter for sub-second precision.
    Results are capped at ``config.max_results``.
    """
    if not config.is_configured:
        return tool_unavailable("mongodb", "Not configured.")

    threshold_microsecs = max(0, threshold_ms * 1000)
    try:
        client = _get_client(config)
        try:
            result = client.admin.command(
                {
                    "currentOp": 1,
                    "microsecs_running": {"$gte": threshold_microsecs},
                }
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
    except Exception as err:
        if getattr(err, "code", None) == 13:
            return tool_unavailable(
                "mongodb",
                "MongoDB user lacks admin privileges for currentOp. Grant the 'clusterMonitor' role.",
            )
        report_validation_failure(
            err,
            logger=logger,
            integration="mongodb",
            method="get_current_ops",
        )
        return tool_unavailable("mongodb", str(err))


def get_rs_status(config: MongoDBConfig) -> dict[str, Any]:
    """Retrieve replica set status (member states, oplog lag).

    Read-only: uses ``replSetGetStatus`` admin command.
    Returns empty members list if the server is not part of a replica set.
    """
    if not config.is_configured:
        return tool_unavailable("mongodb", "Not configured.")

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
    except Exception as err:
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
        if getattr(err, "code", None) == 13:
            return tool_unavailable(
                "mongodb",
                "MongoDB user lacks admin privileges for replSetGetStatus. Grant the 'clusterMonitor' role.",
            )
        report_validation_failure(
            err,
            logger=logger,
            integration="mongodb",
            method="get_rs_status",
        )
        return tool_unavailable("mongodb", error_str)


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
        return tool_unavailable("mongodb", "Not configured.")
    if not config.database:
        return tool_unavailable("mongodb", "Database name is required for profiler data.")

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
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="mongodb",
            method="get_profiler_data",
        )
        return tool_unavailable("mongodb", str(err))


def get_collection_stats(
    config: MongoDBConfig,
    collection: str,
) -> dict[str, Any]:
    """Retrieve statistics for a specific collection.

    Read-only: uses the ``collStats`` command.
    """
    if not config.is_configured:
        return tool_unavailable("mongodb", "Not configured.")
    if not config.database:
        return tool_unavailable("mongodb", "Database name is required for collection stats.")
    if not collection:
        return tool_unavailable("mongodb", "Collection name is required.")

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
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="mongodb",
            method="get_collection_stats",
        )
        return tool_unavailable("mongodb", str(err))


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[MongoDBConfig | None, str | None]:
    try:
        cfg = build_mongodb_config(
            {
                "connection_string": credentials.get("connection_string", ""),
                "database": credentials.get("database", ""),
                "auth_source": credentials.get("auth_source", "admin"),
                "tls": credentials.get("tls", True),
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="mongodb", record_id=record_id)
        return None, None
    if cfg.connection_string:
        return cfg, "mongodb"
    return None, None
