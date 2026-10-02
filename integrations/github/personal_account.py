"""GitHub credentials the OpenSRE account manages, as opposed to manual setup."""

from __future__ import annotations

from typing import Any

from integrations.store import get_integration, remove_integration

#: Tag on a GitHub instance written by account login or workspace sync.
ACCOUNT_AUTH_SOURCE = "opensre_account"


def is_account_managed(integration: dict[str, Any] | None) -> bool:
    """True when ``integration`` was written by the account, so sync may replace it."""
    instances = integration.get("instances") if integration else None
    first = instances[0] if isinstance(instances, list) and instances else None
    tags = first.get("tags") if isinstance(first, dict) else None
    return isinstance(tags, dict) and tags.get("auth_source") == ACCOUNT_AUTH_SOURCE


def disconnect_personal_github() -> bool:
    """Remove a GitHub integration tagged as account-managed; leave manual ones."""
    if not is_account_managed(get_integration("github")):
        return False
    return remove_integration("github")


__all__ = ["ACCOUNT_AUTH_SOURCE", "disconnect_personal_github", "is_account_managed"]
