"""Helpers to select instances from a ``resolved_integrations`` dict.

``classify_integrations`` publishes:
- ``resolved[service]`` — flat config dict of the DEFAULT (first) instance
  (backward compat with all code written before multi-instance support)
- ``resolved[f"_all_{service}_instances"]`` — list of all instances as
  ``[{"name": str, "tags": dict, "config": dict, "integration_id": str}, ...]``
  (only present when > 1 instance OR an instance has a non-``default`` name)

These helpers give consumers a clean API for multi-instance selection. They
never mutate the input and tolerate missing keys (returning ``None`` or ``[]``
as appropriate).
"""

from __future__ import annotations

from typing import Any


def _instances_key(service: str) -> str:
    return f"_all_{service}_instances"


def get_instances(
    resolved: dict[str, Any] | None, service: str
) -> list[dict[str, Any]]:
    """Return all instance entries for ``service``.

    When a service has only one instance and that instance has the default
    name, ``_all_{service}_instances`` is omitted for tidiness. In that
    case we synthesise a single-entry list from the flat ``resolved[service]``
    so callers can treat single- and multi-instance cases uniformly.
    """
    if not resolved:
        return []
    explicit = resolved.get(_instances_key(service))
    if isinstance(explicit, list):
        return [item for item in explicit if isinstance(item, dict)]
    flat = resolved.get(service)
    if isinstance(flat, dict):
        return [
            {
                "name": "default",
                "tags": {},
                "config": flat,
                "integration_id": str(flat.get("integration_id", "")),
            }
        ]
    return []


def get_default_instance(
    resolved: dict[str, Any] | None, service: str
) -> dict[str, Any] | None:
    """Return the flat config dict for the default (first) instance, or None."""
    if not resolved:
        return None
    flat = resolved.get(service)
    return flat if isinstance(flat, dict) else None


def get_instance_by_name(
    resolved: dict[str, Any] | None, service: str, name: str
) -> dict[str, Any] | None:
    """Return the config dict of the instance named ``name``, or None."""
    target = (name or "").strip().lower()
    if not target:
        return None
    for inst in get_instances(resolved, service):
        if str(inst.get("name", "")).lower() == target:
            config = inst.get("config")
            return config if isinstance(config, dict) else None
    return None


def get_instances_by_tag(
    resolved: dict[str, Any] | None, service: str, key: str, value: str
) -> list[dict[str, Any]]:
    """Return config dicts of every instance whose ``tags[key] == value``."""
    target_key = (key or "").strip().lower()
    target_value = (value or "").strip().lower()
    if not target_key or not target_value:
        return []
    out: list[dict[str, Any]] = []
    for inst in get_instances(resolved, service):
        tags = inst.get("tags", {}) if isinstance(inst.get("tags"), dict) else {}
        if tags.get(target_key) == target_value:
            config = inst.get("config")
            if isinstance(config, dict):
                out.append(config)
    return out


def select_instance(
    resolved: dict[str, Any] | None,
    service: str,
    *,
    name: str | None = None,
    tags: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Select an instance by name, then by tags, then default.

    ``name`` takes precedence if supplied. If ``name`` is set but no match is
    found, return None (do NOT silently fall back to default — callers who
    want that can chain to ``get_default_instance``). If ``tags`` is supplied
    without ``name``, return the first instance whose tags are a superset of
    the filter. If neither is supplied, return the default instance.
    """
    if name:
        return get_instance_by_name(resolved, service, name)
    if tags:
        normalized = {str(k).strip().lower(): str(v).strip().lower() for k, v in tags.items()}
        for inst in get_instances(resolved, service):
            inst_tags = inst.get("tags", {}) if isinstance(inst.get("tags"), dict) else {}
            if all(inst_tags.get(k) == v for k, v in normalized.items()):
                config = inst.get("config")
                if isinstance(config, dict):
                    return config
        return None
    return get_default_instance(resolved, service)


__all__ = [
    "get_default_instance",
    "get_instance_by_name",
    "get_instances",
    "get_instances_by_tag",
    "select_instance",
]
