"""Turn Neon API payloads into compact, agent-facing incident summaries.

Each function takes a :class:`NeonClient`, never raises for an expected API
failure, and returns either a result dict or the shared unavailable envelope
with the right failure state (missing config, denied, no match, transport).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from core.tool_framework.utils import (
    tool_access_denied,
    tool_missing_config,
    tool_no_match,
    tool_transport_failure,
)
from integrations.neon.client import NeonAPIError, NeonClient

SOURCE = "neon"
DEFAULT_OPERATIONS_LIMIT = 20
MAX_OPERATIONS_LIMIT = 100
# Neon operation statuses that mean the control plane gave up on a change.
FAILED_OPERATION_STATUSES = frozenset({"failed", "error"})
# Statuses that mean work is still queued or running against the project.
PENDING_OPERATION_STATUSES = frozenset({"running", "scheduling", "cancelling"})


def _failure(exc: NeonAPIError) -> dict[str, Any]:
    status = exc.status_code
    if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        return tool_access_denied(SOURCE, str(exc))
    if exc.invalid_input or status == HTTPStatus.NOT_FOUND:
        return tool_no_match(SOURCE, str(exc))
    return tool_transport_failure(SOURCE, str(exc))


def _not_configured(client: NeonClient) -> dict[str, Any] | None:
    if not client.config.is_configured:
        return tool_missing_config(
            SOURCE, "Neon is not configured. Set NEON_API_KEY and NEON_PROJECT_ID."
        )
    return None


def _clamp(value: int, *, low: int, high: int) -> int:
    return max(low, min(int(value), high))


def _branch_summary(branch: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": branch.get("id", ""),
        "name": branch.get("name", ""),
        "default": bool(branch.get("default", False)),
        "protected": bool(branch.get("protected", False)),
        "current_state": branch.get("current_state", ""),
        "pending_state": branch.get("pending_state"),
        "parent_id": branch.get("parent_id"),
        "logical_size_bytes": branch.get("logical_size"),
        "expires_at": branch.get("expires_at"),
        "updated_at": branch.get("updated_at", ""),
    }


def _endpoint_summary(endpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": endpoint.get("id", ""),
        "branch_id": endpoint.get("branch_id", ""),
        "type": endpoint.get("type", ""),
        "host": endpoint.get("host", ""),
        "current_state": endpoint.get("current_state", ""),
        "pending_state": endpoint.get("pending_state"),
        "disabled": bool(endpoint.get("disabled", False)),
        "autoscaling_limit_min_cu": endpoint.get("autoscaling_limit_min_cu"),
        "autoscaling_limit_max_cu": endpoint.get("autoscaling_limit_max_cu"),
        "suspend_timeout_seconds": endpoint.get("suspend_timeout_seconds"),
        "pooler_enabled": bool(endpoint.get("pooler_enabled", False)),
        "last_active": endpoint.get("last_active"),
        "started_at": endpoint.get("started_at"),
        "suspended_at": endpoint.get("suspended_at"),
    }


def _operation_summary(operation: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "id": operation.get("id", ""),
        "action": operation.get("action", ""),
        "status": operation.get("status", ""),
        "branch_id": operation.get("branch_id"),
        "endpoint_id": operation.get("endpoint_id"),
        "failures_count": operation.get("failures_count", 0),
        "created_at": operation.get("created_at", ""),
        "updated_at": operation.get("updated_at", ""),
        "total_duration_ms": operation.get("total_duration_ms"),
    }
    if operation.get("error"):
        summary["error"] = str(operation["error"])
    return summary


def get_project_status(
    client: NeonClient,
    *,
    branch_id: str = "",
    operations_limit: int = DEFAULT_OPERATIONS_LIMIT,
) -> dict[str, Any]:
    """Project settings, branches, computes, and recent control-plane operations.

    ``branch_id`` narrows computes and operations to one branch; the branch
    list always covers the whole project so a missing or renamed branch shows.
    """
    if (missing := _not_configured(client)) is not None:
        return missing
    limit = _clamp(operations_limit, low=1, high=MAX_OPERATIONS_LIMIT)
    branch_filter = (branch_id or "").strip()
    max_results = client.config.max_results
    try:
        with client:
            project = client.get_project()
            branches = client.list_branches()
            endpoints = client.list_endpoints()
            operations = client.list_operations(limit)
    except NeonAPIError as exc:
        return _failure(exc)

    if branch_filter:
        endpoints = [item for item in endpoints if item.get("branch_id") == branch_filter]
        operations = [item for item in operations if item.get("branch_id") == branch_filter]

    endpoint_rows = [_endpoint_summary(item) for item in endpoints]
    operation_rows = [_operation_summary(item) for item in operations]
    raw_settings = project.get("settings")
    settings: dict[str, Any] = raw_settings if isinstance(raw_settings, dict) else {}
    raw_allowed_ips = settings.get("allowed_ips")
    allowed_ips = raw_allowed_ips.get("ips", []) if isinstance(raw_allowed_ips, dict) else []
    return {
        "source": SOURCE,
        "available": True,
        "project": {
            "id": project.get("id", client.config.project_id),
            "name": project.get("name", ""),
            "region_id": project.get("region_id", ""),
            "pg_version": project.get("pg_version"),
            "created_at": project.get("created_at", ""),
            "compute_last_active_at": project.get("compute_last_active_at"),
            "history_retention_seconds": project.get("history_retention_seconds"),
            "allowed_ips": allowed_ips,
            "block_public_connections": settings.get("block_public_connections"),
            "maintenance_window": settings.get("maintenance_window"),
        },
        "branch_filter": branch_filter or None,
        "total_branches": len(branches),
        "branches": [_branch_summary(item) for item in branches[:max_results]],
        "branches_truncated": len(branches) > max_results,
        "total_endpoints": len(endpoint_rows),
        "endpoints": endpoint_rows[:max_results],
        "endpoints_truncated": len(endpoint_rows) > max_results,
        "active_endpoints": [
            row["id"] for row in endpoint_rows if row["current_state"] == "active"
        ],
        "disabled_endpoints": [row["id"] for row in endpoint_rows if row["disabled"]],
        "operations": operation_rows,
        "failed_operations": [
            row for row in operation_rows if row["status"] in FAILED_OPERATION_STATUSES
        ],
        "pending_operations": [
            row for row in operation_rows if row["status"] in PENDING_OPERATION_STATUSES
        ],
    }


def get_storage_buckets(client: NeonClient, *, branch_id: str = "") -> dict[str, Any]:
    """Object Storage state and buckets for one branch (default: the configured one)."""
    if (missing := _not_configured(client)) is not None:
        return missing
    max_results = client.config.max_results
    try:
        with client:
            branch = client.resolve_branch_id(branch_id)
            storage = client.get_branch_storage(branch)
            buckets = client.list_buckets(branch) if storage.get("enabled", True) else []
    except NeonAPIError as exc:
        return _failure(exc)
    rows = [
        {
            "name": item.get("name", ""),
            "access_level": item.get("access_level", ""),
            "created_at": item.get("created_at", ""),
        }
        for item in buckets
    ]
    return {
        "source": SOURCE,
        "available": True,
        "project_id": client.config.project_id,
        "branch_id": branch,
        "storage_enabled": bool(storage.get("enabled", False)),
        "s3_endpoint": storage.get("s3_endpoint", ""),
        "region": storage.get("region", ""),
        "force_path_style": storage.get("force_path_style"),
        "total_buckets": len(rows),
        "buckets": rows[:max_results],
        "truncated": len(rows) > max_results,
        "public_buckets": [row["name"] for row in rows if row["access_level"] != "private"],
    }


def list_storage_objects(
    client: NeonClient,
    *,
    bucket: str,
    branch_id: str = "",
    prefix: str = "",
    recursive: bool = False,
    limit: int = 0,
    cursor: str = "",
) -> dict[str, Any]:
    """One page of objects (and collapsed folders unless ``recursive``) in a bucket."""
    if (missing := _not_configured(client)) is not None:
        return missing
    if not (bucket or "").strip():
        return tool_missing_config(SOURCE, "A bucket name is required.")
    page_size = _clamp(limit or client.config.max_results, low=1, high=200)
    try:
        with client:
            branch = client.resolve_branch_id(branch_id)
            page = client.list_objects(
                branch,
                bucket,
                prefix=prefix,
                delimiter="" if recursive else "/",
                limit=page_size,
                cursor=cursor,
            )
    except NeonAPIError as exc:
        return _failure(exc)
    objects = [
        {
            "key": item.get("key", ""),
            "size": item.get("size"),
            "last_modified": item.get("last_modified", ""),
            "etag": item.get("etag", ""),
        }
        for item in page.get("objects") or []
        if isinstance(item, dict)
    ]
    folders = [str(item) for item in page.get("folders") or []]
    return {
        "source": SOURCE,
        "available": True,
        "branch_id": branch,
        "bucket": bucket.strip(),
        "prefix": page.get("prefix", prefix),
        "folders": folders,
        "objects": objects,
        "returned": len(objects) + len(folders),
        "truncated": bool(page.get("is_truncated", False)),
        "next_cursor": page.get("next_cursor") or None,
    }


__all__ = [
    "DEFAULT_OPERATIONS_LIMIT",
    "FAILED_OPERATION_STATUSES",
    "MAX_OPERATIONS_LIMIT",
    "PENDING_OPERATION_STATUSES",
    "get_project_status",
    "get_storage_buckets",
    "list_storage_objects",
]
