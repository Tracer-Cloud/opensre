"""Mirror the workspace's GitHub connection from the webapp into the local store.

A signed-in CLI asks the webapp whether its organization has GitHub connected
(``GET /api/auth/cli/integrations/github``). A connection made or updated in the
webapp is written locally as an account-managed instance; a disconnect removes
it. A GitHub integration the user set up by hand is never replaced or removed —
it keeps winning, and the result says so.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Literal

import httpx

from config.account import load_account_record, resolve_account_token
from config.constants.account import (
    OPENSRE_ACCOUNT_GITHUB_PATH,
    OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
)
from integrations.github.personal_account import ACCOUNT_AUTH_SOURCE, is_account_managed
from integrations.store import get_integration, remove_integration, upsert_integration

logger = logging.getLogger(__name__)

GitHubWorkspaceSyncStatus = Literal[
    "connected",
    "updated",
    "unchanged",
    "removed",
    "not_connected",
    "local_override",
    "signed_out",
    "unavailable",
]


@dataclass(frozen=True)
class GitHubWorkspaceSyncResult:
    """What a sync changed locally, plus where the user manages the connection."""

    status: GitHubWorkspaceSyncStatus
    username: str = ""
    settings_url: str = ""
    permissions_url: str = ""

    @property
    def changed(self) -> bool:
        """Whether the local GitHub integration was written or removed."""
        return self.status in {"connected", "updated", "removed"}


def describe_github_sync(result: GitHubWorkspaceSyncResult) -> str:
    """One line telling the user what the sync did and what to do next."""
    who = f" as @{result.username}" if result.username else ""
    manage = f" Manage it at {result.settings_url}." if result.settings_url else ""
    messages: dict[GitHubWorkspaceSyncStatus, str] = {
        "connected": f"GitHub connected from your OpenSRE workspace{who}.{manage}",
        "updated": f"GitHub connection updated from your OpenSRE workspace{who}.{manage}",
        "unchanged": f"GitHub is up to date with your OpenSRE workspace{who}.",
        "removed": "GitHub was disconnected in your OpenSRE workspace and removed here.",
        "not_connected": (
            "Your OpenSRE workspace has no GitHub connection."
            + (f" Connect it at {result.settings_url}." if result.settings_url else "")
        ),
        "local_override": (
            f"Your workspace has GitHub connected{who}, but this machine keeps its own "
            "GitHub setup. Remove it with `opensre integrations remove github` to use "
            "the workspace connection."
        ),
        "signed_out": "Sign in with `opensre account login` to use your workspace integrations.",
        "unavailable": "Could not reach the OpenSRE webapp; local integrations are unchanged.",
    }
    return messages[result.status]


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _managed_credentials(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = payload.get("credentials")
    if not isinstance(raw, Mapping):
        return None
    token = _text(raw, "auth_token")
    if not token:
        return None
    from integrations.github.mcp import (
        DEFAULT_GITHUB_MCP_MODE,
        DEFAULT_GITHUB_MCP_TOOLSETS,
        DEFAULT_GITHUB_MCP_URL,
    )

    return {
        "mode": _text(raw, "mode") or DEFAULT_GITHUB_MCP_MODE,
        "url": _text(raw, "url") or DEFAULT_GITHUB_MCP_URL,
        "auth_token": token,
        "toolsets": list(DEFAULT_GITHUB_MCP_TOOLSETS),
        "username": _text(payload, "username"),
    }


def _current_credentials(integration: dict[str, Any] | None) -> dict[str, Any] | None:
    instances = integration.get("instances") if integration else None
    first = instances[0] if isinstance(instances, list) and instances else None
    credentials = first.get("credentials") if isinstance(first, dict) else None
    return credentials if isinstance(credentials, dict) else None


def _managed_tags(permissions_url: str) -> dict[str, str]:
    tags = {"auth_source": ACCOUNT_AUTH_SOURCE}
    if permissions_url.startswith("https://github.com/"):
        tags["permissions_url"] = permissions_url
    return tags


def _stored_permissions_url(integration: dict[str, Any] | None) -> str:
    instances = integration.get("instances") if integration else None
    first = instances[0] if isinstance(instances, list) and instances else None
    tags = first.get("tags") if isinstance(first, dict) else None
    url = tags.get("permissions_url") if isinstance(tags, dict) else ""
    return url if isinstance(url, str) else ""


def fetch_workspace_github(
    *, http_get: Callable[..., httpx.Response] = httpx.get
) -> Mapping[str, Any] | None:
    """Return the webapp's GitHub status payload, or ``None`` when signed out or unreachable."""
    record = load_account_record()
    token = resolve_account_token()
    if record is None or not token:
        return None
    try:
        response = http_get(
            f"{record.app_url.rstrip('/')}{OPENSRE_ACCOUNT_GITHUB_PATH}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.debug("[github-sync] webapp unreachable", exc_info=True)
        return None
    if response.status_code != HTTPStatus.OK:
        logger.debug("[github-sync] webapp returned HTTP %s", response.status_code)
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, Mapping) else None


@dataclass(frozen=True)
class GitHubWorkspaceShareResult:
    """Whether the webapp stored the shared connection and the hosted agent received it."""

    ok: bool
    delivered_to_agent: bool = False
    username: str = ""
    settings_url: str = ""


def share_github_with_workspace(
    auth_token: str, *, http_post: Callable[..., httpx.Response] = httpx.post
) -> GitHubWorkspaceShareResult:
    """Make this GitHub connection the workspace's, so the hosted agent can use it.

    The caller asks the user first: every workspace member's agent will act
    with this token.
    """
    record = load_account_record()
    token = resolve_account_token()
    if record is None or not token or not auth_token.strip():
        return GitHubWorkspaceShareResult(ok=False)
    try:
        response = http_post(
            f"{record.app_url.rstrip('/')}{OPENSRE_ACCOUNT_GITHUB_PATH}",
            json={"auth_token": auth_token.strip()},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.debug("[github-sync] share failed", exc_info=True)
        return GitHubWorkspaceShareResult(ok=False)
    if response.status_code != HTTPStatus.OK:
        logger.debug("[github-sync] share returned HTTP %s", response.status_code)
        return GitHubWorkspaceShareResult(ok=False)
    try:
        payload = response.json()
    except ValueError:
        return GitHubWorkspaceShareResult(ok=False)
    if not isinstance(payload, Mapping):
        return GitHubWorkspaceShareResult(ok=False)
    return GitHubWorkspaceShareResult(
        ok=payload.get("connected") is True,
        delivered_to_agent=payload.get("delivered_to_agent") is True,
        username=_text(payload, "username"),
        settings_url=_text(payload, "settings_url"),
    )


def _may_write(still_wanted: Callable[[], bool] | None) -> bool:
    if still_wanted is not None and not still_wanted():
        return False
    return load_account_record() is not None and bool(resolve_account_token())


def sync_workspace_github(
    *,
    http_get: Callable[..., httpx.Response] = httpx.get,
    replace_manual: bool = False,
    still_wanted: Callable[[], bool] | None = None,
) -> GitHubWorkspaceSyncResult:
    """Reconcile the local GitHub integration with the workspace connection.

    ``replace_manual`` lets a user who chose the workspace connection in setup
    swap out their manual one; it is replaced only once valid workspace
    credentials are in hand, so a failed fetch never leaves them with nothing.

    The webapp call can be slow. Credentials are written only if the account is
    still signed in and ``still_wanted`` (when given) still agrees at that
    moment, so a sync that outlives a logout or shell exit cannot put a token
    back.
    """
    if load_account_record() is None or not resolve_account_token():
        return GitHubWorkspaceSyncResult(status="signed_out")
    payload = fetch_workspace_github(http_get=http_get)
    if payload is None:
        return GitHubWorkspaceSyncResult(status="unavailable")

    links = {
        "username": _text(payload, "username"),
        "settings_url": _text(payload, "settings_url"),
        "permissions_url": _text(payload, "permissions_url"),
    }
    local = get_integration("github")
    managed = is_account_managed(local)
    connected = payload.get("connected") is True
    credentials = _managed_credentials(payload) if connected else None
    if connected and credentials is None:
        # Malformed "connected" answer: keep whatever works locally.
        return GitHubWorkspaceSyncResult(status="unavailable", **links)

    if credentials is None:
        if managed:
            remove_integration("github")
            return GitHubWorkspaceSyncResult(status="removed", **links)
        return GitHubWorkspaceSyncResult(status="not_connected", **links)
    if local is not None and not managed and not replace_manual:
        return GitHubWorkspaceSyncResult(status="local_override", **links)
    tags = _managed_tags(links["permissions_url"])
    if (
        managed
        and _current_credentials(local) == credentials
        and _stored_permissions_url(local) == tags.get("permissions_url", "")
    ):
        return GitHubWorkspaceSyncResult(status="unchanged", **links)

    if not _may_write(still_wanted):
        return GitHubWorkspaceSyncResult(status="unavailable", **links)
    upsert_integration(
        "github",
        {
            "instances": [
                {
                    "name": "default",
                    "tags": tags,
                    "credentials": credentials,
                }
            ]
        },
    )
    return GitHubWorkspaceSyncResult(status="updated" if managed else "connected", **links)


__all__ = [
    "GitHubWorkspaceShareResult",
    "GitHubWorkspaceSyncResult",
    "GitHubWorkspaceSyncStatus",
    "describe_github_sync",
    "fetch_workspace_github",
    "share_github_with_workspace",
    "sync_workspace_github",
]
