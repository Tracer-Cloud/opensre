"""Shared Redis integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for Redis instances.  All operations are production-safe: read-only,
timeouts enforced, result sizes capped, and key discovery uses the
non-blocking ``SCAN`` cursor.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from config.constants.redis import (
    REDIS_DATABASE_ENV,
    REDIS_HOST_ENV,
    REDIS_PASSWORD_ENV,
    REDIS_PORT_ENV,
    REDIS_SSL_ENV,
    REDIS_USERNAME_ENV,
)
from config.llm_credentials import resolve_env_credential
from config.strict_config import StrictConfigModel
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.coercion import safe_int
from integrations._validation_helpers import report_classify_failure, report_validation_failure
from integrations.config_models import RedisIntegrationConfig

logger = logging.getLogger(__name__)

DEFAULT_REDIS_PORT = 6379
DEFAULT_REDIS_MAX_RESULTS = 50
DEFAULT_REDIS_TIMEOUT_SECONDS = 5.0

# Hard cap on how many keys SCAN will iterate
DEFAULT_REDIS_SCAN_LIMIT = 10_000

# Per-element character cap when sampling list/queue values, so a queue of
# large JSON payloads can never blow up the tool response.
DEFAULT_REDIS_LIST_VALUE_PREVIEW = 256


class RedisConfig(StrictConfigModel):
    """Normalized Redis connection settings."""

    host: str = ""
    port: int = Field(default=DEFAULT_REDIS_PORT, ge=1, le=65535)
    username: str = ""
    password: str = ""
    db: int = Field(default=0, ge=0)
    ssl: bool = False
    timeout_seconds: float = Field(default=DEFAULT_REDIS_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_REDIS_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("host", mode="before")
    @classmethod
    def _normalize_host(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("password", mode="before")
    @classmethod
    def _normalize_password(cls, value: Any) -> str:
        return str(value or "").strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.host)


@dataclass(frozen=True)
class RedisValidationResult:
    """Result of validating a Redis integration."""

    ok: bool
    detail: str


def build_redis_config(raw: dict[str, Any] | None) -> RedisConfig:
    """Build a normalized Redis config object from env/store data."""
    return RedisConfig.model_validate(raw or {})


def redis_config_from_env() -> RedisConfig | None:
    """Load a Redis config from env vars."""
    host = os.getenv(REDIS_HOST_ENV, "").strip()
    if not host:
        return None

    return build_redis_config(
        {
            "host": host,
            "port": safe_int(
                os.getenv(REDIS_PORT_ENV, str(DEFAULT_REDIS_PORT)), DEFAULT_REDIS_PORT
            ),
            "username": os.getenv(REDIS_USERNAME_ENV, "").strip(),
            "password": resolve_env_credential(REDIS_PASSWORD_ENV) or "",
            "db": safe_int(os.getenv(REDIS_DATABASE_ENV, "0"), 0),
            "ssl": os.getenv(REDIS_SSL_ENV, "false").strip().lower() in ("true", "1", "yes"),
        }
    )


def _get_client(config: RedisConfig) -> Any:
    """Create a redis client from config. Caller must close.

    The client decodes responses to ``str`` so callers receive plain Python
    types rather than raw bytes.
    """
    import redis

    return redis.Redis(
        host=config.host,
        port=config.port,
        db=config.db,
        username=config.username or None,
        password=config.password or None,
        ssl=config.ssl,
        socket_timeout=config.timeout_seconds,
        socket_connect_timeout=config.timeout_seconds,
        decode_responses=True,
        client_name="opensre",
    )


def validate_redis_config(config: RedisConfig) -> RedisValidationResult:
    """Validate Redis connectivity with a lightweight ``PING`` command."""
    if not config.host:
        return RedisValidationResult(ok=False, detail="Redis host is required.")

    try:
        client = _get_client(config)
        try:
            if client.ping() is not True:
                return RedisValidationResult(
                    ok=False, detail="Redis PING returned an unexpected result."
                )
            info = client.info("server")
            version = info.get("redis_version", "unknown")
            return RedisValidationResult(
                ok=True,
                detail=(
                    f"Connected to Redis {version} at {config.host}:{config.port}; "
                    f"database {config.db}."
                ),
            )
        finally:
            client.close()
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="redis",
            method="validate_redis_config",
        )
        return RedisValidationResult(ok=False, detail=f"Redis connection failed: {err}")


def redis_is_available(sources: dict[str, dict]) -> bool:
    """Check if Redis integration params are present in available sources."""
    return bool(sources.get("redis", {}).get("host"))


def redis_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract Redis connection params from resolved integrations.

    Credentials are resolved from the integration store or environment, so the
    LLM never needs to supply the host or password directly.
    """
    rd = sources.get("redis", {})
    return {
        "host": str(rd.get("host", "")).strip(),
        "port": int(rd.get("port", DEFAULT_REDIS_PORT) or DEFAULT_REDIS_PORT),
        "username": str(rd.get("username", "")).strip(),
        "password": str(rd.get("password", "")).strip(),
        "db": int(rd.get("db", 0) or 0),
        "ssl": bool(rd.get("ssl", False)),
    }


def get_server_info(config: RedisConfig) -> dict[str, Any]:
    """Retrieve server info: memory, connected clients, keyspace, and stats.

    Read-only: uses the ``INFO`` command.
    """
    if not config.is_configured:
        return tool_unavailable("redis", "Not configured.")

    try:
        client = _get_client(config)
        try:
            info = client.info()
        finally:
            client.close()
    except Exception as err:
        return _redis_error(err, "get_server_info")

    keyspace = {
        db_name: {
            "keys": db_stats.get("keys", 0),
            "expires": db_stats.get("expires", 0),
            "avg_ttl_ms": db_stats.get("avg_ttl", 0),
        }
        for db_name, db_stats in info.items()
        if db_name.startswith("db") and isinstance(db_stats, dict)
    }
    return {
        "source": "redis",
        "available": True,
        "version": info.get("redis_version", ""),
        "mode": info.get("redis_mode", ""),
        "uptime_seconds": info.get("uptime_in_seconds", 0),
        "memory": {
            "used_memory_bytes": info.get("used_memory", 0),
            "used_memory_human": info.get("used_memory_human", ""),
            "used_memory_rss_bytes": info.get("used_memory_rss", 0),
            "used_memory_peak_bytes": info.get("used_memory_peak", 0),
            "maxmemory_bytes": info.get("maxmemory", 0),
            "maxmemory_policy": info.get("maxmemory_policy", ""),
            "mem_fragmentation_ratio": info.get("mem_fragmentation_ratio", 0),
        },
        "clients": {
            "connected_clients": info.get("connected_clients", 0),
            "blocked_clients": info.get("blocked_clients", 0),
            "tracking_clients": info.get("tracking_clients", 0),
        },
        "stats": {
            "total_connections_received": info.get("total_connections_received", 0),
            "total_commands_processed": info.get("total_commands_processed", 0),
            "instantaneous_ops_per_sec": info.get("instantaneous_ops_per_sec", 0),
            "keyspace_hits": info.get("keyspace_hits", 0),
            "keyspace_misses": info.get("keyspace_misses", 0),
            "expired_keys": info.get("expired_keys", 0),
            "evicted_keys": info.get("evicted_keys", 0),
            "rejected_connections": info.get("rejected_connections", 0),
        },
        "keyspace": keyspace,
    }


def get_slowlog(config: RedisConfig, limit: int | None = None) -> dict[str, Any]:
    """Retrieve recent slow log entries.

    Read-only: uses ``SLOWLOG GET``.  Durations are reported in microseconds
    (as Redis stores them).  Results are capped at ``config.max_results``.
    """
    if not config.is_configured:
        return tool_unavailable("redis", "Not configured.")

    effective_limit = min(limit or config.max_results, config.max_results)
    try:
        client = _get_client(config)
        try:
            raw_entries = client.slowlog_get(effective_limit)
        finally:
            client.close()
    except Exception as err:
        return _redis_error(err, "get_slowlog")

    entries = []
    for entry in raw_entries:
        command = entry.get("command", "")
        if isinstance(command, (bytes, bytearray)):
            command = command.decode("utf-8", "replace")
        entries.append(
            {
                "id": entry.get("id"),
                "start_time": entry.get("start_time"),
                "duration_microseconds": entry.get("duration", 0),
                "command": str(command),
                "client_address": entry.get("client_address", ""),
                "client_name": entry.get("client_name", ""),
            }
        )
    return {
        "source": "redis",
        "available": True,
        "returned_entries": len(entries),
        "entries": entries,
    }


def get_replication(config: RedisConfig) -> dict[str, Any]:
    """Retrieve replication status and replica lag.

    Read-only: uses ``INFO replication``.  Reports the node role, master link
    health (for replicas), and per-replica offset lag (for masters).
    """
    if not config.is_configured:
        return tool_unavailable("redis", "Not configured.")

    try:
        client = _get_client(config)
        try:
            info = client.info("replication")
        finally:
            client.close()
    except Exception as err:
        return _redis_error(err, "get_replication")

    role = info.get("role", "")
    master_repl_offset = info.get("master_repl_offset", 0)
    result: dict[str, Any] = {
        "source": "redis",
        "available": True,
        "role": role,
        "connected_slaves": info.get("connected_slaves", 0),
        "master_repl_offset": master_repl_offset,
    }

    if role == "slave":
        slave_offset = info.get("slave_repl_offset", 0)
        result["master"] = {
            "host": info.get("master_host", ""),
            "port": info.get("master_port", 0),
            "link_status": info.get("master_link_status", ""),
            "last_io_seconds_ago": info.get("master_last_io_seconds_ago", -1),
            "sync_in_progress": bool(info.get("master_sync_in_progress", 0)),
            "slave_repl_offset": slave_offset,
        }

    replicas = []
    for key, value in info.items():
        if not key.startswith("slave") or not isinstance(value, dict):
            continue
        replica_offset = value.get("offset", 0)
        replicas.append(
            {
                "id": key,
                "ip": value.get("ip", ""),
                "port": value.get("port", 0),
                "state": value.get("state", ""),
                "offset": replica_offset,
                "lag_bytes": max(0, master_repl_offset - replica_offset),
            }
        )
    result["replicas"] = replicas
    return result


def scan_keys(
    config: RedisConfig,
    pattern: str = "*",
    sample_limit: int | None = None,
) -> dict[str, Any]:
    """Count keys matching a pattern and sample their TTL and type.

    Read-only: uses the non-blocking ``SCAN`` cursor (never ``KEYS``) so the
    server is not blocked on large keyspaces.  Total iteration is capped at
    ``DEFAULT_REDIS_SCAN_LIMIT``; TTL/type sampling is capped at
    ``config.max_results``.
    """
    if not config.is_configured:
        return tool_unavailable("redis", "Not configured.")

    match = pattern or "*"
    sample_cap = min(sample_limit or config.max_results, config.max_results)
    try:
        client = _get_client(config)
        try:
            cursor, matched = 0, 0
            samples: list[dict[str, Any]] = []
            while True:
                sampled_key_names: list[str] = []
                cursor, batch = client.scan(cursor=cursor, match=match, count=100)
                for key in batch:
                    matched += 1
                    if len(samples) + len(sampled_key_names) < sample_cap:
                        sampled_key_names.append(key)
                    if matched >= DEFAULT_REDIS_SCAN_LIMIT:
                        break

                if sampled_key_names:
                    pipe = client.pipeline(transaction=False)
                    for key in sampled_key_names:
                        pipe.ttl(key)
                        pipe.type(key)
                    pipe_results = pipe.execute()
                    for index, key in enumerate(sampled_key_names):
                        samples.append(
                            {
                                "key": key,
                                "ttl_seconds": pipe_results[2 * index],
                                "type": pipe_results[2 * index + 1],
                            }
                        )

                if cursor == 0 or matched >= DEFAULT_REDIS_SCAN_LIMIT:
                    break
        finally:
            client.close()
    except Exception as err:
        return _redis_error(err, "scan_keys")

    return {
        "source": "redis",
        "available": True,
        "pattern": match,
        "matched_keys": matched,
        "scan_truncated": matched >= DEFAULT_REDIS_SCAN_LIMIT,
        "scan_limit": DEFAULT_REDIS_SCAN_LIMIT,
        "sampled_keys": len(samples),
        "samples": samples,
    }


def _truncate_value(value: Any, limit: int = DEFAULT_REDIS_LIST_VALUE_PREVIEW) -> str:
    """Stringify a sampled value and cap its length so payloads stay bounded."""
    text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}…"


def get_client_list(config: RedisConfig) -> dict[str, Any]:
    """Summarize connected clients via ``CLIENT LIST`` (read-only).

    Surfaces connection-pool pressure during an incident: total client count,
    how many are blocked (waiting on ``BLPOP``/``BRPOP``/``XREAD`` etc.), how
    many are in pub/sub mode, and breakdowns by source address and last
    command. The full client list is parsed for the aggregate counts, but only
    ``config.max_results`` clients are returned in the per-client sample so the
    response stays bounded even on a saturated server with thousands of
    connections.
    """
    if not config.is_configured:
        return tool_unavailable("redis", "Not configured.")

    try:
        client = _get_client(config)
        try:
            raw_clients = client.client_list()
        finally:
            client.close()
    except Exception as err:
        return _redis_error(err, "get_client_list")

    blocked = 0
    pubsub = 0
    max_idle_seconds = 0
    by_address: dict[str, int] = {}
    by_command: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    for entry in raw_clients:
        flags = str(entry.get("flags", ""))
        subscriptions = safe_int(entry.get("sub", 0), 0) + safe_int(entry.get("psub", 0), 0)
        idle = safe_int(entry.get("idle", 0), 0)
        addr = str(entry.get("addr", ""))
        # addr is "ip:port" (or "[v6]:port"); group by the host portion only.
        source = addr.rsplit(":", 1)[0] if ":" in addr else (addr or "unknown")
        command = str(entry.get("cmd", "") or "unknown")
        is_blocked = "b" in flags
        is_pubsub = "P" in flags or subscriptions > 0

        if is_blocked:
            blocked += 1
        if is_pubsub:
            pubsub += 1
        max_idle_seconds = max(max_idle_seconds, idle)
        by_address[source] = by_address.get(source, 0) + 1
        by_command[command] = by_command.get(command, 0) + 1

        if len(samples) < config.max_results:
            samples.append(
                {
                    "id": safe_int(entry.get("id", 0), 0),
                    "addr": addr,
                    "name": str(entry.get("name", "")),
                    "age_seconds": safe_int(entry.get("age", 0), 0),
                    "idle_seconds": idle,
                    "flags": flags,
                    "db": safe_int(entry.get("db", 0), 0),
                    "command": command,
                    "user": str(entry.get("user", "")),
                    "blocked": is_blocked,
                    "pubsub": is_pubsub,
                }
            )

    def _top(counts: dict[str, int]) -> dict[str, int]:
        return dict(
            sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[: config.max_results]
        )

    return {
        "source": "redis",
        "available": True,
        "total_clients": len(raw_clients),
        "blocked_clients": blocked,
        "pubsub_clients": pubsub,
        "max_idle_seconds": max_idle_seconds,
        "address_breakdown": _top(by_address),
        "command_breakdown": _top(by_command),
        "returned_clients": len(samples),
        "clients": samples,
    }


def get_list_depth(
    config: RedisConfig,
    key: str,
    head: int = 0,
    tail: int = 0,
) -> dict[str, Any]:
    """Report a list/queue key's depth (``LLEN``) with an optional bounded sample.

    Read-only. ``TYPE`` is checked first so a non-list key returns a clear
    message instead of a ``WRONGTYPE`` error, and a missing key reports
    ``exists=False`` rather than being indistinguishable from an empty list.
    Head/tail sampling uses bounded ``LRANGE`` (each side capped at
    ``config.max_results``); every sampled element is truncated so a queue of
    large job payloads cannot blow up the response.
    """
    if not config.is_configured:
        return tool_unavailable("redis", "Not configured.")
    key = str(key or "").strip()
    if not key:
        return tool_unavailable("redis", "A list key is required.")

    head_n = max(0, min(head or 0, config.max_results))
    tail_n = max(0, min(tail or 0, config.max_results))
    try:
        client = _get_client(config)
        try:
            key_type = client.type(key)
            if key_type == "none":
                return {
                    "source": "redis",
                    "available": True,
                    "key": key,
                    "exists": False,
                    "type": "none",
                    "depth": 0,
                    "head": [],
                    "tail": [],
                }
            if key_type != "list":
                return {
                    "source": "redis",
                    "available": True,
                    "key": key,
                    "exists": True,
                    "type": key_type,
                    "depth": None,
                    "head": [],
                    "tail": [],
                    "error": f"Key '{key}' is a {key_type}, not a list.",
                }
            pipe = client.pipeline(transaction=False)
            pipe.llen(key)
            if head_n:
                pipe.lrange(key, 0, head_n - 1)
            if tail_n:
                pipe.lrange(key, -tail_n, -1)
            results = pipe.execute()
        finally:
            client.close()
    except Exception as err:
        return _redis_error(err, "get_list_depth")

    depth = results[0]
    cursor = 1
    head_values: list[Any] = []
    if head_n:
        head_values = results[cursor]
        cursor += 1
    tail_values: list[Any] = results[cursor] if tail_n else []

    return {
        "source": "redis",
        "available": True,
        "key": key,
        "exists": True,
        "type": "list",
        "depth": depth,
        "head": [_truncate_value(value) for value in head_values],
        "tail": [_truncate_value(value) for value in tail_values],
    }


def get_latency_doctor(
    config: RedisConfig,
    event: str = "",
    history_limit: int | None = None,
) -> dict[str, Any]:
    """Return ``LATENCY DOCTOR`` analysis plus the latest monitored events.

    Read-only. ``LATENCY DOCTOR`` produces a human-readable diagnosis of recent
    latency spikes (fork/RDB save, AOF rewrite, blocking commands, slow disk).
    ``LATENCY LATEST`` lists each monitored event's latest/max spike; when
    ``event`` is given, ``LATENCY HISTORY`` for that event is included (capped at
    ``history_limit`` or ``config.max_results``). ``monitoring_active`` reflects
    whether latency monitoring is *enabled* (``latency-monitor-threshold`` > 0),
    read via ``CONFIG GET`` — so a healthy, enabled-but-quiet server is reported
    as active even when no spikes have been recorded yet. The threshold read is
    best-effort: if the ACL forbids ``CONFIG GET`` it falls back to whether any
    events exist, and ``monitoring_threshold_ms`` is ``None``.
    """
    if not config.is_configured:
        return tool_unavailable("redis", "Not configured.")

    event = str(event or "").strip()
    # Floor at 0 (mirrors get_list_depth's head/tail clamp): a negative
    # history_limit is truthy, so without max(0, ...) it would survive the min()
    # and turn ``history_raw[:history_cap]`` into a back-truncating slice that
    # silently drops the most recent events instead of capping the count.
    history_cap = max(0, min(history_limit or config.max_results, config.max_results))
    try:
        client = _get_client(config)
        try:
            # redis-py intentionally does not implement latency_doctor() (its
            # output is human-readable, not parseable), so issue the raw command.
            report = client.execute_command("LATENCY", "DOCTOR")
            latest_raw = client.latency_latest()
            history_raw = client.latency_history(event) if event else []
            # Best-effort read of whether monitoring is enabled. CONFIG GET is
            # read-only (only CONFIG SET is out of scope); guard it separately so
            # an ACL that forbids CONFIG doesn't fail the whole tool.
            threshold_ms: int | None = None
            try:
                cfg = client.config_get("latency-monitor-threshold")
                threshold_ms = safe_int(cfg.get("latency-monitor-threshold", 0), 0)
            except Exception:
                threshold_ms = None
        finally:
            client.close()
    except Exception as err:
        return _redis_error(err, "get_latency_doctor")

    latest = [
        {
            "event": str(row[0]),
            "last_occurrence": row[1],
            "latest_ms": row[2],
            "max_ms": row[3],
        }
        for row in latest_raw
        if isinstance(row, (list, tuple)) and len(row) >= 4
    ]
    history = [
        {"timestamp": row[0], "latency_ms": row[1]}
        for row in history_raw[:history_cap]
        if isinstance(row, (list, tuple)) and len(row) >= 2
    ]

    monitoring_active = threshold_ms > 0 if threshold_ms is not None else bool(latest)
    return {
        "source": "redis",
        "available": True,
        "report": str(report),
        "monitoring_active": monitoring_active,
        "monitoring_threshold_ms": threshold_ms,
        "monitored_events": len(latest),
        "latest": latest,
        "event": event,
        "history": history,
    }


def _redis_error(err: Exception, method: str) -> dict[str, Any]:
    """Normalize a Redis exception into a graceful, available=False payload.

    Authentication and permission failures return a friendly hint without a
    Sentry report; all other errors are reported for diagnosis.  Errors are
    classified by redis-py's typed exceptions rather than message substrings.
    """
    import redis.exceptions as redis_exc

    if isinstance(err, redis_exc.AuthenticationError):
        return tool_unavailable(
            "redis",
            "Redis authentication failed. Check the credentials in the connection settings.",
        )
    if isinstance(err, redis_exc.NoPermissionError):
        return tool_unavailable(
            "redis",
            "Redis user lacks permission for this command. Grant the user read "
            "access to the diagnostic commands it needs (e.g. INFO, CLIENT, "
            "SLOWLOG, LATENCY, LLEN/LRANGE, TYPE, SCAN).",
        )
    report_validation_failure(
        err,
        logger=logger,
        integration="redis",
        method=method,
    )
    return tool_unavailable("redis", str(err))


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[RedisIntegrationConfig | None, str | None]:
    try:
        cfg = RedisIntegrationConfig.model_validate(
            {
                "host": credentials.get("host", ""),
                "port": credentials.get("port", 6379),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "db": credentials.get("db", 0),
                "ssl": credentials.get("ssl", False),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="redis", record_id=record_id)
        return None, None
    if cfg.host:
        return cfg, "redis"
    return None, None
