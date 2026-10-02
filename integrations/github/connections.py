"""Preserve connection identity and select one GitHub grant before tools resolve."""

from __future__ import annotations

from typing import Any

from integrations.github.mcp import classify, github_mcp_is_usably_configured


def classify_github_connections(records: list[dict[str, Any]], resolved: dict[str, Any]) -> None:
    """Managed records include inactive authority markers, without their secrets."""
    github = [record for record in records if record.get("service") == "github"]
    managed = any(
        "is_default" in instance.get("credentials", {})
        for record in github
        for instance in record.get("instances", [])
    )
    if not managed:
        return
    connections: list[dict[str, Any]] = []
    for record in github:
        for instance in record.get("instances", []):
            credentials = instance.get("credentials", {})
            config, _ = classify(credentials, str(record.get("id", "")))
            usable = (
                record.get("status") == "active"
                and config is not None
                and github_mcp_is_usably_configured(config)
            )
            connections.append(
                {
                    "name": instance.get("name", record["id"]),
                    "tags": instance.get("tags", {}),
                    "integration_id": record["id"],
                    "connection_id": record["id"],
                    "is_default": str(credentials.get("is_default", "false")).lower() == "true",
                    "config": config.model_dump() if usable and config else {},
                    "available": usable,
                }
            )
    resolved["_all_github_instances"] = connections
    resolved["_github_managed_connections"] = True
    resolved.update(select_github_connection(resolved, None))


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
        selected["github"] = dict(matches[0]["config"])
    else:
        selected["github"] = {
            "connection_verified": False,
            "connection_selection_error": "github_connection_unavailable"
            if matches
            else "github_connection_required",
            "connection_id": connection_id or "",
        }
    return selected
