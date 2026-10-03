"""Resolve configured credentials afresh in the background worker."""

from __future__ import annotations

from collections.abc import Mapping

from config.constants import GH_TOKEN_ENV, GITHUB_MCP_AUTH_TOKEN_ENV, GITHUB_TOKEN_ENV
from config.llm_credentials import resolve_env_credential
from integrations.catalog import resolve_effective_integrations
from integrations.github.helpers import github_creds


def configured_token(explicit: str | None = None, *, connection_id: str | None = None) -> str:
    """Re-resolve a selection exactly; otherwise use normal credential precedence."""
    token = effective_github_token(explicit, connection_id=connection_id)
    if token:
        return token
    if connection_id:
        raise ValueError("The selected GitHub connection is unavailable; repair stopped.")
    raise ValueError("Configure GitHub with `opensre integrations setup github` before scheduling.")


def effective_github_token(explicit: str | None = None, *, connection_id: str | None = None) -> str:
    """Resolve a GitHub token from any configured source; ``""`` when absent.

    A selected connection is resolved exactly and never falls back to the
    injected token, another stored connection, or an environment credential.
    """
    if connection_id:
        return _selected_github_token(connection_id)
    if explicit:
        return explicit
    token = stored_github_token()
    if token:
        return token
    for name in (GITHUB_MCP_AUTH_TOKEN_ENV, GITHUB_TOKEN_ENV, GH_TOKEN_ENV):
        token = resolve_env_credential(name)
        if token:
            return token
    return ""


def _selected_github_token(connection_id: str) -> str:
    """Resolve one selected grant through the same source precedence as the host."""
    from infrastructure.harness_providers import resolve_integrations

    github = resolve_integrations({"github_connection_id": connection_id}).get("github")
    if not isinstance(github, Mapping):
        return ""
    if github.get("connection_selection_error"):
        return ""
    if str(github.get("connection_id") or "") != connection_id:
        return ""
    creds = github_creds(dict(github))
    return str(creds.get("github_token") or "")


def stored_github_token() -> str:
    """Token of the effective GitHub integration; its entry wraps the classified config."""
    github = resolve_effective_integrations().get("github", {})
    candidate = github.get("config")
    config = candidate if isinstance(candidate, dict) else {}
    if not config:
        return ""
    creds = github_creds(config)
    return str(creds.get("github_token") or "")


def account_id(user: Mapping[str, object]) -> int:
    """Require the stable GitHub account ID before authorizing a durable run."""
    value = user.get("id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("GitHub did not return a valid account identity.")
    return value
