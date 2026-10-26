"""The eligible app token shared by GitHub tools and skill prerequisites."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from config.constants.github import GITHUB_CONNECTION_ORIGIN_TAG, GITHUB_WEBAPP_ORIGIN
from core.tool import availability_view


def github_selection_failed(sources: Mapping[str, Any]) -> bool:
    """True when the chosen GitHub grant is missing or unusable.

    ``select_github_connection`` marks that on ``sources["github"]``; GitHub
    tools are then withdrawn and no other token stands in for the grant.
    """
    github = sources.get("github")
    return isinstance(github, Mapping) and bool(github.get("connection_selection_error"))


def github_rest_token(
    sources: Mapping[str, Any] | None = None,
    *,
    explicit: str | None = None,
    connection_origin: str | None = None,
) -> str:
    """Return an attested app token, preserving an explicitly empty token."""
    if sources is not None:
        if github_selection_failed(sources):
            return ""
        github = sources.get("github")
        if not isinstance(github, Mapping):
            return ""
        connection_origin = github.get(GITHUB_CONNECTION_ORIGIN_TAG)
        if explicit is None:
            explicit = str(github.get("github_token") or github.get("auth_token") or "")
    if connection_origin != GITHUB_WEBAPP_ORIGIN:
        return ""
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
