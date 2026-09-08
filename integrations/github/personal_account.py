"""Remove GitHub credentials created by older OpenSRE account login."""

from __future__ import annotations

from integrations.store import get_integration, remove_integration

_ACCOUNT_AUTH_SOURCE = "opensre_account"


def disconnect_personal_github() -> bool:
    """Remove a GitHub integration tagged as account-managed; leave manual ones."""
    integration = get_integration("github")
    instances = integration.get("instances") if integration else None
    first = instances[0] if isinstance(instances, list) and instances else None
    tags = first.get("tags") if isinstance(first, dict) else None
    if not isinstance(tags, dict) or tags.get("auth_source") != _ACCOUNT_AUTH_SOURCE:
        return False
    return remove_integration("github")


__all__ = ["disconnect_personal_github"]
