"""Preserve connection identity and select one GitHub grant before tools resolve."""

from __future__ import annotations

from typing import Any

from config.constants.account import (
    INTEGRATION_APP_ORIGIN,
    INTEGRATION_IS_DEFAULT_TAG,
    INTEGRATION_OWNER_ID_TAG,
    INTEGRATION_OWNER_KIND_TAG,
    INTEGRATION_RETRIEVAL_ORIGIN_FIELD,
)
from config.constants.github import (
    GITHUB_CONNECTION_ORIGIN_TAG,
    GITHUB_LOCAL_ORIGIN,
    GITHUB_UNKNOWN_ORIGIN,
    GITHUB_WEBAPP_ORIGIN,
)
from integrations.github.mcp import classify


def classify_github_connections(records: list[dict[str, Any]], resolved: dict[str, Any]) -> None:
    """Index local grants and managed inactive markers by their record identity."""
    github = [record for record in records if record.get("service") == "github"]
    if not github:
        return
    connections: list[dict[str, Any]] = []
    for record in github:
        record_id = str(record.get("id", ""))
        for instance in _instances(record):
            credentials = instance.get("credentials", {})
            tags = instance.get("tags", {})
            tags = tags if isinstance(tags, dict) else {}
            config, _ = classify(credentials, record_id)
            origin = (
                tags.get(GITHUB_CONNECTION_ORIGIN_TAG, GITHUB_UNKNOWN_ORIGIN)
                if record.get(INTEGRATION_RETRIEVAL_ORIGIN_FIELD) == INTEGRATION_APP_ORIGIN
                else GITHUB_LOCAL_ORIGIN
            )
            usable = (
                origin == GITHUB_WEBAPP_ORIGIN
                and record.get("status") == "active"
                and config is not None
                and bool(config.auth_token.strip())
            )
            connections.append(
                {
                    GITHUB_CONNECTION_ORIGIN_TAG: origin,
                    "name": instance.get("name", record_id),
                    "tags": tags,
                    "integration_id": record_id,
                    "connection_id": record_id,
                    "owner_kind": str(tags.get(INTEGRATION_OWNER_KIND_TAG, "")),
                    "owner_id": str(tags.get(INTEGRATION_OWNER_ID_TAG, "")),
                    "is_default": str(
                        tags.get(INTEGRATION_IS_DEFAULT_TAG, credentials.get("is_default", "false"))
                    ).lower()
                    == "true",
                    "config": config.model_dump() if usable and config else {},
                    "available": usable,
                }
            )
    resolved["_all_github_instances"] = connections
    resolved["_github_managed_connections"] = True
    resolved.update(select_github_connection(resolved, None))


def _instances(record: dict[str, Any]) -> list[dict[str, Any]]:
    instances = record.get("instances")
    if isinstance(instances, list):
        return [dict(instance) for instance in instances if isinstance(instance, dict)]
    return [
        {
            "name": "default",
            "tags": {
                GITHUB_CONNECTION_ORIGIN_TAG: record.get(
                    GITHUB_CONNECTION_ORIGIN_TAG, GITHUB_UNKNOWN_ORIGIN
                )
            },
            "credentials": record.get("credentials", {}),
        }
    ]


def select_github_connection(resolved: dict[str, Any], connection_id: str | None) -> dict[str, Any]:
    """Select one GitHub grant, or disable GitHub when that choice is unusable.

    An id that is not in this process's grants is ignored only when exactly one
    grant is available. A matched grant that is not available, or several grants
    with no single default, disables GitHub without falling back to another account.
    A personal default outranks a workspace default.
    """
    selected = dict(resolved)
    if "_all_github_instances" not in resolved:
        return selected
    instances = list(resolved.get("_all_github_instances", []))
    rejected = [
        item for item in instances if item.get(GITHUB_CONNECTION_ORIGIN_TAG) != GITHUB_WEBAPP_ORIGIN
    ]
    if connection_id and _has_connection(rejected, connection_id):
        selected["github"] = {
            "connection_verified": False,
            "connection_selection_error": "github_connection_required",
            "connection_id": connection_id,
        }
        return selected
    instances = [
        item for item in instances if item.get(GITHUB_CONNECTION_ORIGIN_TAG) == GITHUB_WEBAPP_ORIGIN
    ]
    if connection_id and not _has_connection(instances, connection_id):
        available = [item for item in instances if _grant_available(item)]
        if len(available) == 1:
            connection_id = None
    matches = _matching_grants(instances, connection_id)
    if len(matches) == 1 and _grant_available(matches[0]):
        selected["github"] = {
            **matches[0]["config"],
            GITHUB_CONNECTION_ORIGIN_TAG: GITHUB_WEBAPP_ORIGIN,
            "connection_id": matches[0].get("connection_id", matches[0].get("integration_id", "")),
        }
    else:
        selected["github"] = {
            "connection_verified": False,
            "connection_selection_error": "github_connection_unavailable"
            if matches
            else "github_connection_required",
            "connection_id": connection_id or "",
        }
    return selected


def _has_connection(instances: list[dict[str, Any]], connection_id: str) -> bool:
    return any(_connection_id(item) == connection_id for item in instances)


def _matching_grants(
    instances: list[dict[str, Any]], connection_id: str | None
) -> list[dict[str, Any]]:
    if connection_id:
        return [item for item in instances if _connection_id(item) == connection_id]
    defaults = [item for item in instances if item.get("is_default") is True]
    if defaults:
        # Each owner may mark one default; the user's own choice outranks the
        # workspace's, so a personal default never makes the pick ambiguous.
        personal = [item for item in defaults if item.get("owner_kind") == "user"]
        return personal or defaults
    available = [item for item in instances if _grant_available(item)]
    if len(available) == 1:
        return available
    return []


def _connection_id(item: dict[str, Any]) -> str:
    return str(item.get("connection_id", item.get("integration_id", "")))


def _grant_available(item: dict[str, Any]) -> bool:
    return item.get(GITHUB_CONNECTION_ORIGIN_TAG) == GITHUB_WEBAPP_ORIGIN and bool(
        item.get("available", bool(item.get("config")))
    )


def preserve_app_github_records(
    merged: list[dict[str, Any]], *groups: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep live remote GitHub records ahead of local service-level overrides."""
    remote = {
        str(record.get("id", "")): record
        for group in groups
        for record in group
        if record.get("service") == "github"
        and record.get(INTEGRATION_RETRIEVAL_ORIGIN_FIELD) == INTEGRATION_APP_ORIGIN
    }
    if not remote:
        return merged
    return [record for record in merged if record.get("service") != "github"] + list(
        remote.values()
    )


def filter_github_connected_services(
    services: list[str], records: list[dict[str, Any]]
) -> list[str]:
    """Advertise GitHub only when the same app selection used by tools qualifies."""
    from integrations.github.rest_token import github_rest_token

    resolved: dict[str, Any] = {}
    classify_github_connections(records, resolved)
    eligible = bool(github_rest_token(resolved))
    return [service for service in services if service != "github" or eligible]
