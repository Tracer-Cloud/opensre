"""Credential and scope plumbing shared by the Neon tools.

The API key, project ID, and API base URL are injected from the resolved
``neon`` integration and hidden from the model, so a tool call can never point
the key at another project or host. ``branch_id`` is a default the model may
override to look at another branch of the same project.
"""

from __future__ import annotations

from typing import Any

from integrations.neon.client import NeonClient
from integrations.neon.config import build_neon_config

INJECTED_PARAMS = ("api_key", "project_id", "base_url")

INJECTED_SCHEMA_PROPERTIES: dict[str, dict[str, Any]] = {
    "api_key": {"type": "string", "description": "Neon API key (injected)."},
    "project_id": {"type": "string", "description": "Neon project ID (injected)."},
    "base_url": {"type": "string", "description": "Neon API base URL (injected)."},
}

BRANCH_ID_PROPERTY: dict[str, Any] = {
    "type": "string",
    "default": "",
    "description": (
        "Neon branch ID (br-...). Defaults to the configured branch, else the "
        "project's default branch."
    ),
}


def neon_is_available(sources: dict[str, Any]) -> bool:
    """True when a resolved Neon integration carries a key and a project."""
    neon = sources.get("neon") or {}
    return bool(neon.get("api_key") and neon.get("project_id"))


def neon_extract_params(sources: dict[str, Any]) -> dict[str, Any]:
    """Injected credentials plus the configured default branch."""
    neon = sources.get("neon") or {}
    return {
        "api_key": str(neon.get("api_key", "")),
        "project_id": str(neon.get("project_id", "")),
        "base_url": str(neon.get("base_url", "")),
        "branch_id": str(neon.get("branch_id", "")),
    }


def neon_client(api_key: str, project_id: str, base_url: str = "") -> NeonClient:
    """A client for the injected project."""
    return NeonClient(
        build_neon_config({"api_key": api_key, "project_id": project_id, "base_url": base_url})
    )


__all__ = [
    "BRANCH_ID_PROPERTY",
    "INJECTED_PARAMS",
    "INJECTED_SCHEMA_PROPERTIES",
    "neon_client",
    "neon_extract_params",
    "neon_is_available",
]
