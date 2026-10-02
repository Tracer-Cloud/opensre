"""Preserve connection identity and select one GitHub grant before tools resolve."""

from __future__ import annotations

from typing import Any

from integrations.github.mcp import classify, github_mcp_is_usably_configured


def classify_github_connections(records: list[dict[str, Any]], resolved: dict[str, Any]) -> None:
    """Index local grants and managed inactive markers by their record identity."""
    github = [record for record in records if record.get("service") == "github"]
    managed = any(
        "is_default" in instance.get("credentials", {})
        for record in github
        for instance in _instances(record)
    )
    if not github:
        return
    connections: list[dict[str, Any]] = []
    for record in github:
        record_id = str(record.get("id", ""))
        for instance in _instances(record):
            credentials = instance.get("credentials", {})
            config, _ = classify(credentials, record_id)
            usable = (
                record.get("status") == "active"
                and config is not None
                and github_mcp_is_usably_configured(config)
            )
            connections.append(
                {
                    "name": instance.get("name", record_id),
                    "tags": instance.get("tags", {}),
                    "integration_id": record_id,
                    "connection_id": record_id,
                    "is_default": str(credentials.get("is_default", "false")).lower() == "true",
                    "config": config.model_dump() if usable and config else {},
                    "available": usable,
                }
            )
    resolved["_all_github_instances"] = connections
    if managed:
        resolved["_github_managed_connections"] = True
        resolved.update(select_github_connection(resolved, None))


def _instances(record: dict[str, Any]) -> list[dict[str, Any]]:
    instances = record.get("instances")
    if isinstance(instances, list):
        return [dict(instance) for instance in instances if isinstance(instance, dict)]
    return [{"name": "default", "credentials": record.get("credentials", {})}]


def select_github_connection(resolved: dict[str, Any], connection_id: str | None) -> dict[str, Any]:
    """An invalid explicit/default choice disables GitHub without account fallback."""
    selected = dict(resolved)
    instances = resolved.get("_all_github_instances", [])
    if not connection_id and not resolved.get("_github_managed_connections"):
        return selected
    matches = [
        item
        for item in instances
        if (
            item.get("connection_id", item.get("integration_id")) == connection_id
            if connection_id
            else item.get("is_default") is True
        )
    ]
    if len(matches) == 1 and matches[0].get("available", bool(matches[0].get("config"))):
        selected["github"] = {
            **matches[0]["config"],
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
