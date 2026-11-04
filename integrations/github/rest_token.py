"""The eligible app token shared by GitHub tools and skill prerequisites."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from config.constants.github import (
    GH_TOKEN_ENV,
    GITHUB_CONNECTION_ORIGIN_TAG,
    GITHUB_TOKEN_ENV,
    GITHUB_WEBAPP_ORIGIN,
)
from core.tool import availability_view


def github_selection_failed(sources: Mapping[str, Any]) -> bool:
    """True when the chosen GitHub grant is missing or unusable.

    ``select_github_connection`` marks that on ``sources["github"]``; GitHub
    tools are then withdrawn and no other token stands in for the grant.
    """
    github = sources.get("github")
    return isinstance(github, Mapping) and bool(github.get("connection_selection_error"))


#: Opt-in: let a gateway that is NOT tethered to the OpenSRE app use a GitHub
#: token from its own environment. A webapp-origin grant (the only kind the
#: upstream flow accepts) can never be minted on an untethered self-host, so
#: without this every GitHub tool is withdrawn (github_connection_required).
_ALLOW_LOCAL_GITHUB_TOKEN_ENV = "OPENSRE_GITHUB_ALLOW_LOCAL_TOKEN"
_TRUTHY = {"1", "true", "yes", "on"}


def _allow_local_github_token() -> bool:
    """True only when the local-token opt-in is explicitly set."""
    return os.getenv(_ALLOW_LOCAL_GITHUB_TOKEN_ENV, "").strip().lower() in _TRUTHY


def _env_github_token() -> str:
    """The GitHub token from the environment (GITHUB_TOKEN, then GH_TOKEN)."""
    return (os.getenv(GITHUB_TOKEN_ENV) or os.getenv(GH_TOKEN_ENV) or "").strip()


def _local_github_fallback(explicit: str | None) -> str:
    """Self-host fallback used when no attested webapp grant is available.

    Returns "" (the upstream default) unless the opt-in is set, so a
    webapp-tethered deployment is unaffected. When enabled, prefer the token
    already resolved for this call and otherwise the environment token.
    """
    if not _allow_local_github_token():
        return ""
    return (explicit or "").strip() or _env_github_token()


def github_rest_token(
    sources: Mapping[str, Any] | None = None,
    *,
    explicit: str | None = None,
    connection_origin: str | None = None,
) -> str:
    """Return an attested app token, preserving an explicitly empty token."""
    requested_explicit = explicit
    if sources is not None:
        if github_selection_failed(sources):
            return _local_github_fallback(requested_explicit)
        github = sources.get("github")
        if not isinstance(github, Mapping):
            return _local_github_fallback(requested_explicit)
        connection_origin = github.get(GITHUB_CONNECTION_ORIGIN_TAG)
        if explicit is None:
            explicit = str(github.get("github_token") or github.get("auth_token") or "")
    if connection_origin != GITHUB_WEBAPP_ORIGIN:
        return _local_github_fallback(requested_explicit)
    return (explicit or "").strip()


def resolved_github_rest_token(resolved_integrations: Mapping[str, Any]) -> str:
    """The token a GitHub REST tool run on ``resolved_integrations`` is handed; ``""`` when none."""
    return github_rest_token(availability_view(dict(resolved_integrations)))


def has_github_rest_token(resolved_integrations: Mapping[str, Any]) -> bool:
    """True when a GitHub REST tool run on ``resolved_integrations`` would find a token."""
    return bool(resolved_github_rest_token(resolved_integrations))


__all__ = [
    "github_rest_token",
    "github_selection_failed",
    "has_github_rest_token",
    "resolved_github_rest_token",
]
