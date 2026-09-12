"""Resolve configured credentials afresh in the background worker."""

from __future__ import annotations

from config.constants import GH_TOKEN_ENV, GITHUB_MCP_AUTH_TOKEN_ENV, GITHUB_TOKEN_ENV
from config.llm_credentials import resolve_env_credential
from integrations.catalog import resolve_effective_integrations
from integrations.github.helpers import github_creds


def configured_token(explicit: str | None = None) -> str:
    """Prefer injected credentials, then the integration store and credential fallback."""
    if explicit:
        return explicit
    github = resolve_effective_integrations().get("github", {})
    token = str(github_creds(github).get("github_token") or "")
    if token:
        return token
    for name in (GITHUB_MCP_AUTH_TOKEN_ENV, GITHUB_TOKEN_ENV, GH_TOKEN_ENV):
        token = resolve_env_credential(name)
        if token:
            return token
    raise ValueError("Configure GitHub with `opensre integrations setup github` before scheduling.")
