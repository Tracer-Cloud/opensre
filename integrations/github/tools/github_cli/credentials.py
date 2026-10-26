"""Credential extraction for the GitHub CLI tool."""

from __future__ import annotations

from typing import Any

from config.constants.github import GITHUB_CONNECTION_ORIGIN_TAG, GITHUB_WEBAPP_ORIGIN

# Keys ``extract_params`` may inject that must beat model-supplied kwargs.
# Narrower than ``integrations.github.helpers.GITHUB_INJECTED_PARAMS``: the CLI
# protects only the token; owner/repo stay model-overridable.
GITHUB_CLI_INJECTED_PARAMS: tuple[str, ...] = (
    "github_token",
    "github_connection_id",
    "github_connection_origin",
)


def github_source_available(sources: dict[str, dict]) -> bool:
    from integrations.github.rest_token import has_github_rest_token

    return has_github_rest_token(sources)


def github_creds(gh: dict[str, Any]) -> dict[str, Any]:
    """Map classified GitHub integration fields to tool credential kwargs."""
    if gh.get(GITHUB_CONNECTION_ORIGIN_TAG) != GITHUB_WEBAPP_ORIGIN:
        return {}
    creds: dict[str, Any] = {}
    if gh.get("connection_id"):
        creds["github_connection_id"] = gh["connection_id"]
    token = gh.get("github_token") or gh.get("auth_token")
    if token:
        creds["github_token"] = token
    return creds


__all__ = [
    "GITHUB_CLI_INJECTED_PARAMS",
    "github_creds",
    "github_source_available",
]
