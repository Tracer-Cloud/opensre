"""Local integration credential store.

Integrations are stored in ~/.tracer/integrations.json.

File format:
{
  "version": 1,
  "integrations": [
    {
      "id": "grafana-1",
      "service": "grafana",
      "status": "active",
      "credentials": {
        "endpoint": "https://...",
        "api_key": "..."
      }
    },
    {
      "id": "aws-1",
      "service": "aws",
      "status": "active",
      "role_arn": "arn:aws:iam::...",
      "external_id": "...",
      "credentials": {}
    },
    ...
  ]
}

Each entry mirrors the shape returned by /api/integrations so that
_classify_integrations() in node_resolve_integrations can consume both
sources without any special-casing.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.constants import INTEGRATIONS_STORE_PATH

logger = logging.getLogger(__name__)

STORE_PATH = INTEGRATIONS_STORE_PATH
_VERSION = 1


def _load_raw() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return {"version": _VERSION, "integrations": []}
    try:
        data = json.loads(STORE_PATH.read_text())
        if not isinstance(data, dict) or "integrations" not in data:
            return {"version": _VERSION, "integrations": []}
        return data
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to read integrations store at %s", STORE_PATH, exc_info=True)
        return {"version": _VERSION, "integrations": []}


def _save(data: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(data, indent=2) + "\n")


def load_integrations() -> list[dict[str, Any]]:
    """Return all active local integrations."""
    return list(_load_raw().get("integrations", []))


def get_integration(service: str) -> dict[str, Any] | None:
    """Return the first active integration for a service, or None."""
    for i in load_integrations():
        if i.get("service") == service and i.get("status") == "active":
            return i
    return None


def upsert_integration(service: str, entry: dict[str, Any]) -> None:
    """Add or replace the integration for a service.

    entry must contain at minimum: {"credentials": {...}}
    service-specific top-level fields (role_arn, etc.) should be included
    directly in entry.
    """
    data = _load_raw()
    integrations: list[dict[str, Any]] = data.get("integrations", [])

    # Remove existing entry for the same service
    integrations = [i for i in integrations if i.get("service") != service]

    record: dict[str, Any] = {
        "id": f"{service}-{uuid.uuid4().hex[:8]}",
        "service": service,
        "status": "active",
        "credentials": {},
        **entry,
    }
    integrations.append(record)

    data["integrations"] = integrations
    _save(data)


def remove_integration(service: str) -> bool:
    """Remove integration for a service. Returns True if something was removed."""
    data = _load_raw()
    before = len(data.get("integrations", []))
    data["integrations"] = [i for i in data.get("integrations", []) if i.get("service") != service]
    removed = len(data["integrations"]) < before
    if removed:
        _save(data)
    return removed


def list_integrations() -> list[dict[str, Any]]:
    """Return summary info for all stored integrations."""
    return [
        {
            "service": i.get("service"),
            "status": i.get("status"),
            "id": i.get("id"),
        }
        for i in load_integrations()
    ]
