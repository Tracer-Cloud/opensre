"""Shared RabbitMQ integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for RabbitMQ brokers via the HTTP Management API.

All operations are production-safe: read-only, timeouts enforced, result sizes
capped.  The Management API (port 15672 by default) is strongly preferred over
AMQP introspection because it returns rich JSON describing queues, consumers,
connections, channels, nodes, and alarms — exactly the operational state an
incident investigation needs.  AMQP would require a new dependency and expose
far less diagnostic data.

The management plugin (``rabbitmq_management``) must be enabled on the target
broker; the validation helper surfaces that specifically so users aren't left
chasing a generic 404.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx
from pydantic import Field, field_validator

from config.constants.rabbitmq import (
    RABBITMQ_HOST_ENV,
    RABBITMQ_MANAGEMENT_PORT_ENV,
    RABBITMQ_PASSWORD_ENV,
    RABBITMQ_SSL_ENV,
    RABBITMQ_USERNAME_ENV,
    RABBITMQ_VERIFY_SSL_ENV,
    RABBITMQ_VHOST_ENV,
)
from config.strict_config import StrictConfigModel
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.coercion import safe_int
from integrations._validation_helpers import report_classify_failure, report_validation_failure

logger = logging.getLogger(__name__)

DEFAULT_RABBITMQ_MANAGEMENT_PORT = 15672
DEFAULT_RABBITMQ_VHOST = "/"
DEFAULT_RABBITMQ_TIMEOUT_SECONDS = 10
DEFAULT_RABBITMQ_MAX_RESULTS = 50


class RabbitMQConfig(StrictConfigModel):
    """Normalized RabbitMQ Management API connection settings."""

    host: str = ""
    management_port: int = DEFAULT_RABBITMQ_MANAGEMENT_PORT
    username: str = ""
    password: str = ""
    vhost: str = DEFAULT_RABBITMQ_VHOST
    ssl: bool = False
    verify_ssl: bool = True
    timeout_seconds: int = Field(default=DEFAULT_RABBITMQ_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_RABBITMQ_MAX_RESULTS, gt=0, le=200)
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
        # Do NOT strip passwords — leading/trailing whitespace is valid.
        return str(value or "")

    @field_validator("vhost", mode="before")
    @classmethod
    def _normalize_vhost(cls, value: Any) -> str:
        raw = str(value or "").strip()
        return raw or DEFAULT_RABBITMQ_VHOST

    @field_validator("management_port", mode="before")
    @classmethod
    def _normalize_management_port(cls, value: Any) -> int:
        return safe_int(value, DEFAULT_RABBITMQ_MANAGEMENT_PORT)

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.username)

    @property
    def base_url(self) -> str:
        scheme = "https" if self.ssl else "http"
        return f"{scheme}://{self.host}:{self.management_port}"


@dataclass(frozen=True)
class RabbitMQValidationResult:
    """Result of validating a RabbitMQ integration."""

    ok: bool
    detail: str


def build_rabbitmq_config(raw: dict[str, Any] | None) -> RabbitMQConfig:
    """Build a normalized RabbitMQ config object from env/store data."""
    return RabbitMQConfig.model_validate(raw or {})


def rabbitmq_config_from_env() -> RabbitMQConfig | None:
    """Load a RabbitMQ config from env vars."""
    host = os.getenv(RABBITMQ_HOST_ENV, "").strip()
    username = os.getenv(RABBITMQ_USERNAME_ENV, "").strip()
    if not host or not username:
        return None
    return build_rabbitmq_config(
        {
            "host": host,
            "management_port": os.getenv(
                RABBITMQ_MANAGEMENT_PORT_ENV,
                str(DEFAULT_RABBITMQ_MANAGEMENT_PORT),
            ).strip(),
            "username": username,
            "password": os.getenv(RABBITMQ_PASSWORD_ENV, ""),
            "vhost": os.getenv(RABBITMQ_VHOST_ENV, DEFAULT_RABBITMQ_VHOST).strip(),
            "ssl": os.getenv(RABBITMQ_SSL_ENV, "false").strip().lower() in ("true", "1", "yes"),
            "verify_ssl": os.getenv(RABBITMQ_VERIFY_SSL_ENV, "true").strip().lower()
            in ("true", "1", "yes"),
        }
    )


def _get_client(config: RabbitMQConfig) -> httpx.Client:
    """Build an authenticated httpx client for the management API."""
    return httpx.Client(
        base_url=config.base_url,
        auth=(config.username, config.password),
        timeout=float(config.timeout_seconds),
        verify=config.verify_ssl,
    )


def _error_evidence(err: str) -> dict[str, Any]:
    return tool_unavailable("rabbitmq", err)


def _vhost_path(base: str, vhost: str) -> str:
    """Build a vhost-scoped API path, URL-encoding the vhost segment."""
    encoded = urllib.parse.quote(vhost, safe="")
    return f"{base}/{encoded}"


def _http_get(client: httpx.Client, path: str) -> tuple[Any | None, str | None]:
    """Fetch a JSON endpoint; return (data, error_message)."""
    try:
        response = client.get(path)
    except httpx.RequestError as err:
        return None, f"RabbitMQ request failed: {err}"

    if response.status_code == HTTPStatus.UNAUTHORIZED:
        return None, "RabbitMQ authentication failed (check username/password)."
    if response.status_code == HTTPStatus.NOT_FOUND:
        if path == "/api/overview":
            return None, (
                "RabbitMQ Management API not found — enable the "
                "`rabbitmq_management` plugin on the broker: "
                "`rabbitmq-plugins enable rabbitmq_management`."
            )
        return None, f"RabbitMQ endpoint not found: {path}"
    if response.status_code >= HTTPStatus.BAD_REQUEST:
        return None, (
            f"RabbitMQ management API returned HTTP {response.status_code}: {response.text[:200]}"
        )
    try:
        return response.json(), None
    except ValueError as err:
        return None, f"RabbitMQ management API returned non-JSON body: {err}"


def validate_rabbitmq_config(config: RabbitMQConfig) -> RabbitMQValidationResult:
    """Validate RabbitMQ management API reachability with a lightweight call."""
    if not config.host or not config.username:
        return RabbitMQValidationResult(ok=False, detail="RabbitMQ host and username are required.")

    try:
        with _get_client(config) as client:
            data, err = _http_get(client, "/api/overview")
            if err is not None:
                return RabbitMQValidationResult(ok=False, detail=err)
            if not isinstance(data, dict):
                return RabbitMQValidationResult(
                    ok=False,
                    detail="RabbitMQ management API returned an unexpected response.",
                )
            version = str(data.get("rabbitmq_version", "unknown"))
            cluster = str(data.get("cluster_name", "unknown"))
            return RabbitMQValidationResult(
                ok=True,
                detail=(
                    f"Connected to RabbitMQ {version} (cluster: {cluster}, vhost: {config.vhost})."
                ),
            )
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="rabbitmq",
            method="validate_rabbitmq_config",
        )
        return RabbitMQValidationResult(ok=False, detail=f"RabbitMQ connection failed: {err}")


def rabbitmq_is_available(sources: dict[str, dict]) -> bool:
    """Check if RabbitMQ integration credentials are present."""
    rmq = sources.get("rabbitmq", {})
    return bool(rmq.get("host") and rmq.get("username"))


def rabbitmq_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract RabbitMQ credentials from resolved integrations."""
    rmq = sources.get("rabbitmq", {})
    return {
        "host": rmq.get("host", ""),
        "management_port": rmq.get("management_port", DEFAULT_RABBITMQ_MANAGEMENT_PORT),
        "username": rmq.get("username", ""),
        "password": rmq.get("password", ""),
        "vhost": rmq.get("vhost", DEFAULT_RABBITMQ_VHOST),
        "ssl": rmq.get("ssl", False),
        "verify_ssl": rmq.get("verify_ssl", True),
    }


# ---------------------------------------------------------------------------
# Diagnostic query functions.  Each returns the standard evidence dict shape:
#   {"source": "rabbitmq", "available": bool, ...diagnostic-specific keys}
# Errors return {"source": "rabbitmq", "available": False, "error": "..."}.
# ---------------------------------------------------------------------------


def _summarize_queue(queue: dict[str, Any]) -> dict[str, Any]:
    stats = queue.get("message_stats") or {}
    # The Management API returns these count fields as an explicit JSON ``null``
    # (not just absent) for queues without live stats — e.g. a queue whose home
    # node is down, or right after a broker restart before the stats DB is
    # populated. ``dict.get(key, 0)`` only substitutes the default when the key
    # is missing, so ``null`` would flow through as ``None`` and crash the
    # backlog sort below (``None + None``). Coerce with ``or 0``, mirroring the
    # ``message_stats or {}`` guard above.
    return {
        "name": queue.get("name", ""),
        "vhost": queue.get("vhost", ""),
        "state": queue.get("state", "unknown"),
        "messages_ready": queue.get("messages_ready") or 0,
        "messages_unacknowledged": queue.get("messages_unacknowledged") or 0,
        "messages_total": queue.get("messages") or 0,
        "messages_persistent": queue.get("messages_persistent") or 0,
        "consumers": queue.get("consumers") or 0,
        "consumer_utilisation": queue.get("consumer_utilisation"),
        "memory_bytes": queue.get("memory") or 0,
        "publish_rate": (stats.get("publish_details") or {}).get("rate", 0.0),
        "deliver_rate": (stats.get("deliver_get_details") or {}).get("rate", 0.0),
        "ack_rate": (stats.get("ack_details") or {}).get("rate", 0.0),
    }


def get_queue_backlog(
    config: RabbitMQConfig,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Return the top N queues ranked by backlog (unacked + ready)."""
    if not config.is_configured:
        return _error_evidence("Not configured.")

    effective_limit = min(max_results or config.max_results, config.max_results)
    try:
        with _get_client(config) as client:
            path = _vhost_path("/api/queues", config.vhost)
            data, err = _http_get(client, path)
            if err is not None:
                return _error_evidence(err)
            if not isinstance(data, list):
                return _error_evidence(f"Unexpected {path} response.")
            summarized = [_summarize_queue(q) for q in data]
            summarized.sort(
                key=lambda q: q["messages_ready"] + q["messages_unacknowledged"],
                reverse=True,
            )
            truncated = summarized[:effective_limit]
            return {
                "source": "rabbitmq",
                "available": True,
                "total_queues": len(summarized),
                "returned": len(truncated),
                "queues": truncated,
            }
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="rabbitmq",
            method="get_queue_backlog",
        )
        return _error_evidence(str(err))


def get_consumer_health(
    config: RabbitMQConfig,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Return consumer-level diagnostics across vhosts."""
    if not config.is_configured:
        return _error_evidence("Not configured.")

    effective_limit = min(max_results or config.max_results, config.max_results)
    try:
        with _get_client(config) as client:
            path = _vhost_path("/api/consumers", config.vhost)
            data, err = _http_get(client, path)
            if err is not None:
                return _error_evidence(err)
            if not isinstance(data, list):
                return _error_evidence(f"Unexpected {path} response.")
            consumers = []
            for entry in data[:effective_limit]:
                queue = entry.get("queue") or {}
                channel = entry.get("channel_details") or {}
                consumers.append(
                    {
                        "queue": queue.get("name", ""),
                        "vhost": queue.get("vhost", ""),
                        "consumer_tag": entry.get("consumer_tag", ""),
                        "ack_required": bool(entry.get("ack_required", True)),
                        "prefetch_count": entry.get("prefetch_count", 0),
                        "active": entry.get("active", True),
                        "channel_name": channel.get("name", ""),
                        "connection_name": channel.get("connection_name", ""),
                        "peer_host": channel.get("peer_host", ""),
                    }
                )
            return {
                "source": "rabbitmq",
                "available": True,
                "total_consumers": len(data),
                "returned": len(consumers),
                "consumers": consumers,
            }
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="rabbitmq",
            method="get_consumer_health",
        )
        return _error_evidence(str(err))


def get_broker_overview(config: RabbitMQConfig) -> dict[str, Any]:
    """Return cluster-wide overview + alarm status."""
    if not config.is_configured:
        return _error_evidence("Not configured.")

    try:
        with _get_client(config) as client:
            overview, err = _http_get(client, "/api/overview")
            if err is not None:
                return _error_evidence(err)
            if not isinstance(overview, dict):
                return _error_evidence("Unexpected /api/overview response.")

            totals = overview.get("queue_totals") or {}
            msg_stats = overview.get("message_stats") or {}
            object_totals = overview.get("object_totals") or {}

            # /api/health/checks/alarms returns HTTP 503 (not 200) when alarms
            # are active, so we can't use _http_get which treats >=400 as error.
            # This is the modern Health Checks API path (RabbitMQ 3.9+, the
            # project's documented minimum is 3.12+); the older one-word
            # /api/healthchecks/alarms path was removed and 404s on every
            # broker version this integration targets - confirmed live
            # against RabbitMQ 3.13.7, where the old path never worked.
            alarm_payload: dict[str, Any] = {"ok": False, "detail": "unknown"}
            try:
                alarm_resp = client.get("/api/health/checks/alarms")
            except httpx.RequestError as exc:
                alarm_payload = {"ok": False, "detail": str(exc)}
            else:
                if alarm_resp.status_code == HTTPStatus.OK:
                    alarm_payload = {"ok": True, "detail": "ok"}
                elif alarm_resp.status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    alarm_payload = {
                        "ok": None,
                        "detail": "alarm status unknown (insufficient permissions)",
                    }
                elif alarm_resp.status_code == HTTPStatus.NOT_FOUND:
                    alarm_payload = {
                        "ok": None,
                        "detail": "alarm endpoint not available on this broker version",
                    }
                else:
                    # 503 = alarms active; parse the structured reason.
                    try:
                        alarm_body = alarm_resp.json()
                        alarm_payload = {
                            "ok": False,
                            "detail": str(alarm_body.get("reason", alarm_resp.text[:200])),
                        }
                    except ValueError:
                        alarm_payload = {
                            "ok": False,
                            "detail": alarm_resp.text[:200],
                        }

            return {
                "source": "rabbitmq",
                "available": True,
                "cluster_name": overview.get("cluster_name", ""),
                "rabbitmq_version": overview.get("rabbitmq_version", ""),
                "erlang_version": overview.get("erlang_version", ""),
                "messages_ready": totals.get("messages_ready", 0),
                "messages_unacknowledged": totals.get("messages_unacknowledged", 0),
                "messages_total": totals.get("messages", 0),
                "publish_rate": (msg_stats.get("publish_details") or {}).get("rate", 0.0),
                "deliver_rate": (msg_stats.get("deliver_get_details") or {}).get("rate", 0.0),
                "queues": object_totals.get("queues", 0),
                "consumers": object_totals.get("consumers", 0),
                "connections": object_totals.get("connections", 0),
                "channels": object_totals.get("channels", 0),
                "alarms": alarm_payload,
            }
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="rabbitmq",
            method="get_broker_overview",
        )
        return _error_evidence(str(err))


def get_node_health(config: RabbitMQConfig) -> dict[str, Any]:
    """Return per-node resource + partition state (cluster diagnostics)."""
    if not config.is_configured:
        return _error_evidence("Not configured.")

    try:
        with _get_client(config) as client:
            data, err = _http_get(client, "/api/nodes")
            if err is not None:
                return _error_evidence(err)
            if not isinstance(data, list):
                return _error_evidence("Unexpected /api/nodes response.")
            nodes = []
            for node in data:
                nodes.append(
                    {
                        "name": node.get("name", ""),
                        "running": bool(node.get("running", False)),
                        "type": node.get("type", ""),
                        "mem_used_bytes": node.get("mem_used", 0),
                        "mem_limit_bytes": node.get("mem_limit", 0),
                        "mem_alarm": bool(node.get("mem_alarm", False)),
                        "disk_free_bytes": node.get("disk_free", 0),
                        "disk_free_limit_bytes": node.get("disk_free_limit", 0),
                        "disk_free_alarm": bool(node.get("disk_free_alarm", False)),
                        "fd_used": node.get("fd_used", 0),
                        "fd_total": node.get("fd_total", 0),
                        "sockets_used": node.get("sockets_used", 0),
                        "sockets_total": node.get("sockets_total", 0),
                        "proc_used": node.get("proc_used", 0),
                        "proc_total": node.get("proc_total", 0),
                        "partitions": list(node.get("partitions") or []),
                    }
                )
            any_partitioned = any(n["partitions"] for n in nodes)
            return {
                "source": "rabbitmq",
                "available": True,
                "node_count": len(nodes),
                "any_partitioned": any_partitioned,
                "nodes": nodes,
            }
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="rabbitmq",
            method="get_node_health",
        )
        return _error_evidence(str(err))


def get_connection_stats(
    config: RabbitMQConfig,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Return active connection metadata sorted by receive rate."""
    if not config.is_configured:
        return _error_evidence("Not configured.")

    effective_limit = min(max_results or config.max_results, config.max_results)
    try:
        with _get_client(config) as client:
            # /api/connections has no vhost-scoped variant; filter client-side.
            data, err = _http_get(client, "/api/connections")
            if err is not None:
                return _error_evidence(err)
            if not isinstance(data, list):
                return _error_evidence("Unexpected /api/connections response.")

            connections = []
            for conn in data:
                if conn.get("vhost", "/") != config.vhost:
                    continue
                recv_oct = conn.get("recv_oct_details") or {}
                send_oct = conn.get("send_oct_details") or {}
                connections.append(
                    {
                        "name": conn.get("name", ""),
                        "user": conn.get("user", ""),
                        "vhost": conn.get("vhost", ""),
                        "state": conn.get("state", "unknown"),
                        "protocol": conn.get("protocol", ""),
                        "channels": conn.get("channels", 0),
                        "peer_host": conn.get("peer_host", ""),
                        "peer_port": conn.get("peer_port", 0),
                        "ssl": bool(conn.get("ssl", False)),
                        "recv_rate_bytes_per_sec": recv_oct.get("rate", 0.0),
                        "send_rate_bytes_per_sec": send_oct.get("rate", 0.0),
                    }
                )
            connections.sort(key=lambda c: c["recv_rate_bytes_per_sec"], reverse=True)
            truncated = connections[:effective_limit]
            return {
                "source": "rabbitmq",
                "available": True,
                "broker_total_connections": len(data),
                "vhost_connections": len(connections),
                "returned": len(truncated),
                "connections": truncated,
            }
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="rabbitmq",
            method="get_connection_stats",
        )
        return _error_evidence(str(err))


__all__ = [
    "DEFAULT_RABBITMQ_MANAGEMENT_PORT",
    "DEFAULT_RABBITMQ_VHOST",
    "RabbitMQConfig",
    "RabbitMQValidationResult",
    "build_rabbitmq_config",
    "get_broker_overview",
    "get_connection_stats",
    "get_consumer_health",
    "get_node_health",
    "get_queue_backlog",
    "rabbitmq_config_from_env",
    "rabbitmq_extract_params",
    "rabbitmq_is_available",
    "validate_rabbitmq_config",
]


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[RabbitMQConfig | None, str | None]:
    try:
        cfg = build_rabbitmq_config(
            {
                "host": credentials.get("host", ""),
                "management_port": credentials.get("management_port", 15672),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "vhost": credentials.get("vhost", "/"),
                "ssl": credentials.get("ssl", False),
                "verify_ssl": credentials.get("verify_ssl", True),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="rabbitmq", record_id=record_id)
        return None, None
    if cfg.host and cfg.username:
        return cfg, "rabbitmq"
    return None, None
