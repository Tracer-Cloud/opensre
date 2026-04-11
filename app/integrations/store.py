"""Local integration credential store.

Integrations are stored in ~/.tracer/integrations.json.

File format (v2 supports multi-instance):
{
  "version": 2,
  "integrations": [
    {
      "id": "grafana-1",
      "service": "grafana",
      "status": "active",
      "instances": [
        {
          "name": "prod",
          "tags": ["prod", "us-east-1"],
          "credentials": {"endpoint": "https://prod.grafana.net", "api_key": "..."}
        },
        {
          "name": "staging",
          "tags": ["staging"],
          "credentials": {"endpoint": "https://staging.grafana.net", "api_key": "..."}
        }
      ]
    },
    ...
  ]
}

For backward compatibility, v1 format (single credentials per service) is still supported.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.constants import INTEGRATIONS_STORE_PATH

logger = logging.getLogger(__name__)

STORE_PATH = INTEGRATIONS_STORE_PATH
_VERSION = 2


def _migrate_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate v1 store format to v2 with instances array."""
    if data.get("version", 1) >= 2:
        return data

    integrations = data.get("integrations", [])
    for integration in integrations:
        if "instances" not in integration:
            integration["instances"] = [
                {
                    "name": "default",
                    "tags": [],
                    "credentials": integration.get("credentials", {}),
                }
            ]
    data["version"] = 2
    return data


def _load_raw() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return {"version": _VERSION, "integrations": []}
    try:
        data = json.loads(STORE_PATH.read_text())
        if not isinstance(data, dict) or "integrations" not in data:
            return {"version": _VERSION, "integrations": []}
        return _migrate_to_v2(data)
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to read integrations store at %s", STORE_PATH, exc_info=True)
        return {"version": _VERSION, "integrations": []}


def _save(data: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(data, indent=2) + "\n")


def load_integrations() -> list[dict[str, Any]]:
    """Return all active local integrations with their instances."""
    return list(_load_raw().get("integrations", []))


def get_integration(
    service: str,
    name: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any] | None:
    """Return the first active integration for a service matching criteria, or None.

    Args:
        service: The service name (e.g., "grafana", "datadog")
        name: Filter by instance name (optional)
        tags: Filter by instance tags (optional, matches if any tag overlaps)

    Returns:
        Integration dict with instances, or None if not found.
    """
    for i in load_integrations():
        if i.get("service") != service or i.get("status") != "active":
            continue

        instances = i.get("instances", [])
        if not instances:
            continue

        if name is None and tags is None:
            return i

        for instance in instances:
            instance_name = instance.get("name", "default")
            instance_tags = instance.get("tags", [])

            if name is not None and instance_name != name:
                continue
            if tags is not None and not set(tags) & set(instance_tags):
                continue

            return i

    return None


def get_integrations(
    service: str,
    name: str | None = None,
    tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return all active integrations for a service matching criteria.

    Args:
        service: The service name
        name: Filter by instance name (optional)
        tags: Filter by instance tags (optional, matches if any tag overlaps)

    Returns:
        List of matching integration dicts (each with instances).
    """
    results = []
    for i in load_integrations():
        if i.get("service") != service or i.get("status") != "active":
            continue

        instances = i.get("instances", [])
        if not instances:
            continue

        if name is None and tags is None:
            results.append(i)
            continue

        for instance in instances:
            instance_name = instance.get("name", "default")
            instance_tags = instance.get("tags", [])

            if name is not None and instance_name != name:
                continue
            if tags is not None and not set(tags) & set(instance_tags):
                continue

            results.append(i)
            break

    return results


def upsert_integration(
    service: str,
    entry: dict[str, Any],
    instance_name: str = "default",
    instance_tags: list[str] | None = None,
) -> None:
    """Add or replace an integration instance for a service.

    Args:
        service: The service name (e.g., "grafana", "datadog")
        entry: Integration data (credentials, role_arn, etc.)
        instance_name: Name for this instance (default: "default")
        instance_tags: Tags for this instance (default: [])
    """
    data = _load_raw()
    integrations: list[dict[str, Any]] = data.get("integrations", [])

    instance_tags = instance_tags or []

    new_instance = {
        "name": instance_name,
        "tags": instance_tags,
        "credentials": entry.get("credentials", {}),
    }

    for i, integration in enumerate(integrations):
        if integration.get("service") == service:
            existing_instances = integration.get("instances", [])

            for idx, inst in enumerate(existing_instances):
                if inst.get("name") == instance_name:
                    existing_instances[idx] = new_instance
                    break
            else:
                existing_instances.append(new_instance)

            integrations[i]["instances"] = existing_instances
            data["integrations"] = integrations
            _save(data)
            return

    record: dict[str, Any] = {
        "id": f"{service}-{uuid.uuid4().hex[:8]}",
        "service": service,
        "status": "active",
        "instances": [new_instance],
    }
    integrations.append(record)

    data["integrations"] = integrations
    _save(data)


def remove_integration(service: str, instance_name: str | None = None) -> bool:
    """Remove integration or specific instance. Returns True if something was removed.

    Args:
        service: The service name
        instance_name: If provided, remove only this instance. Otherwise remove all.
    """
    data = _load_raw()
    before = len(data.get("integrations", []))

    if instance_name is None:
        data["integrations"] = [
            i for i in data.get("integrations", []) if i.get("service") != service
        ]
        removed = len(data["integrations"]) < before
    else:
        removed = False
        for integration in data.get("integrations", []):
            if integration.get("service") == service:
                instances = integration.get("instances", [])
                filtered = [i for i in instances if i.get("name") != instance_name]
                if len(filtered) < len(instances):
                    removed = True
                integration["instances"] = filtered

        data["integrations"] = [
            i
            for i in data.get("integrations", [])
            if i.get("service") != service or i.get("instances")
        ]

    if removed:
        _save(data)
    return removed


def list_integrations() -> list[dict[str, Any]]:
    """Return summary info for all stored integrations with their instances."""
    return [
        {
            "service": i.get("service"),
            "status": i.get("status"),
            "id": i.get("id"),
            "instances": [
                {"name": inst.get("name"), "tags": inst.get("tags", [])}
                for inst in i.get("instances", [])
            ],
        }
        for i in load_integrations()
    ]
