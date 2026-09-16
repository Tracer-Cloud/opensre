"""Lightweight GitHub identity helpers for UI, analytics, and public sources.

Kept separate from :mod:`integrations.github.login` so callers can read saved
identity data or derive public repository scope without importing GitHub MCP.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

_GITHUB_REPOSITORY_PART_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_SCP_REPOSITORY_RE = re.compile(r"^(?:[^@/]+@)?(?P<host>[A-Za-z0-9._-]+):(?P<path>.+)$")
_PATH_DOTS = frozenset({".", ".."})


def is_github_repository_part(value: str) -> bool:
    """True when *value* is one GitHub owner or repository name, not a path."""
    return (
        bool(value)
        and value not in _PATH_DOTS
        and _GITHUB_REPOSITORY_PART_RE.fullmatch(value) is not None
    )


def _workspace_repository_path(value: str) -> str:
    if "://" in value:
        parsed = urlsplit(value)
        return parsed.path.strip("/") if (parsed.hostname or "").lower() in _GITHUB_HOSTS else ""
    scp_match = _SCP_REPOSITORY_RE.fullmatch(value)
    if scp_match is not None:
        if scp_match.group("host").lower() not in _GITHUB_HOSTS:
            return ""
        return scp_match.group("path").strip("/")
    return value.strip("/")


def workspace_public_repository_source(
    runtime_metadata: Mapping[str, Any],
) -> dict[str, dict[str, str | bool]]:
    """Expose a valid workspace GitHub repository for public read-only tools."""
    workspace_repo = _workspace_repository_path(
        str(runtime_metadata.get("workspace_repo") or "").strip()
    ).removesuffix(".git")
    owner, separator, repo = workspace_repo.partition("/")
    if not separator or not is_github_repository_part(owner):
        return {}
    if not is_github_repository_part(repo):
        return {}
    return {
        "github": {
            "connection_verified": False,
            "public_repository": True,
            "owner": owner,
            "repo": repo,
        }
    }


def saved_github_username() -> str:
    """Return the persisted GitHub login from the integration store, or "".

    Best-effort and never raises: callers like the welcome banner and analytics
    re-identify must work even when the store is unreadable.
    """
    try:
        from integrations.store import get_integration

        record = get_integration("github")
        if not record:
            return ""
        credentials = record.get("credentials") or {}
        return str(credentials.get("username") or "").strip()
    except Exception:
        return ""
