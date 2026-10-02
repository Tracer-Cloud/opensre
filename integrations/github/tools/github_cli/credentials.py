"""GitHub credential helpers for the ``tools`` layer.

``tools`` and ``integrations`` are layer peers under ``.importlinter.strict``,
so this module mirrors ``integrations.github`` helpers without importing that
package. Resolution order: explicit/injected token, then MCP env, then REST
env names (fallback for local/dev).
"""

from __future__ import annotations

import os
from typing import Any

from config.constants import GH_TOKEN_ENV, GITHUB_MCP_AUTH_TOKEN_ENV, GITHUB_TOKEN_ENV

# Keys ``extract_params`` may inject that must beat model-supplied kwargs.
# Narrower than ``integrations.github.helpers.GITHUB_INJECTED_PARAMS``: the CLI
# protects only the token; owner/repo stay model-overridable.
GITHUB_CLI_INJECTED_PARAMS: tuple[str, ...] = ("github_token", "github_connection_id")


def resolve_github_token(explicit: str | None = None) -> str:
    """Resolve a GitHub token: explicit → MCP env → GITHUB_TOKEN → GH_TOKEN."""
    return (
        (explicit or "").strip()
        or os.getenv(GITHUB_MCP_AUTH_TOKEN_ENV, "").strip()
        or os.getenv(GITHUB_TOKEN_ENV, "").strip()
        or os.getenv(GH_TOKEN_ENV, "").strip()
    )


def github_source_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("github", {}).get("connection_verified"))


def github_creds(gh: dict[str, Any]) -> dict[str, Any]:
    """Map classified GitHub integration fields to tool credential kwargs."""
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
    "resolve_github_token",
]
