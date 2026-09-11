"""Helpers shared by GitHub MCP tools.

Each function takes inputs produced by the rest of the runtime
(integration store entries, runtime-extracted kwargs)
and returns the shape the next layer expects. Callers live in
``integrations/github/tools/*.py`` and rely on these wrappers so the
tool files stay thin.

Exports:

- ``GITHUB_INJECTED_PARAMS``: kwargs ``extract_params`` may inject that must
  win over model-supplied values at call time.
- ``github_source_available``: predicate on the integration store entry.
- ``github_creds``: maps classified integration fields to tool kwargs.
- ``resolve_github_mcp_config``: merges env defaults with explicit overrides.
"""

from __future__ import annotations

from typing import Any

from integrations.github.client import resolve_github_token
from integrations.github.mcp import (
    DEFAULT_GITHUB_MCP_MODE,
    GitHubMCPConfig,
    build_github_mcp_config,
    github_mcp_config_from_env,
)

# Runtime connection/secret kwargs from ``extract_params``; must win over model input.
GITHUB_INJECTED_PARAMS: tuple[str, ...] = (
    "github_url",
    "github_mode",
    "github_token",
    "github_command",
    "github_args",
)


def github_source_available(sources: dict[str, dict]) -> bool:
    """Return True when the GitHub integration is configured and reachable.

    ``sources`` is the per-integration view assembled by the runtime from the
    integration store; the relevant entry is ``sources["github"]``. Returns
    True only when that entry's ``connection_verified`` flag is set truthy
    (typically by the verifier after a live credentials check). Missing
    ``github`` entry or a falsy/missing ``connection_verified`` returns False.
    """
    return bool(sources.get("github", {}).get("connection_verified"))


def github_credentials_available(sources: dict[str, dict]) -> bool:
    """Return True when a verified source or fallback REST token is available."""
    gh = sources.get("github", {})
    return bool(
        github_source_available(sources)
        or github_creds(gh).get("github_token")
        or resolve_github_token(None)
    )


def github_creds(gh: dict) -> dict[str, Any]:
    """Map classified GitHub integration fields to tool credential kwargs."""
    creds: dict[str, Any] = {}
    url = gh.get("github_url") or gh.get("url")
    if url:
        creds["github_url"] = url
    mode = gh.get("github_mode") or gh.get("mode")
    if mode:
        creds["github_mode"] = mode
    token = gh.get("github_token") or gh.get("auth_token")
    if token:
        creds["github_token"] = token
    command = gh.get("github_command") or gh.get("command")
    if command:
        creds["github_command"] = command
    args = gh.get("github_args")
    if args is None:
        args = gh.get("args")
    if args:
        creds["github_args"] = list(args)
    return creds


def _has_explicit_github_mcp_overrides(
    github_url: str | None,
    github_mode: str | None,
    github_token: str | None,
    github_command: str | None,
    github_args: list[str] | None,
) -> bool:
    if github_url or github_token or github_command or github_args:
        return True
    return bool(github_mode and github_mode != DEFAULT_GITHUB_MCP_MODE)


def resolve_github_mcp_config(
    github_url: str | None,
    github_mode: str | None,
    github_token: str | None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
) -> GitHubMCPConfig | None:
    """Return the GitHub MCP config to use, merging env defaults with overrides.

    Reads ``github_mcp_config_from_env()`` for the env-derived baseline, then
    treats any non-default value among ``github_url``, ``github_token``,
    ``github_command``, ``github_args``, or a non-default ``github_mode`` as an
    explicit override. When no overrides are present, returns the env config
    as-is. Otherwise builds a fresh ``GitHubMCPConfig`` filling unset fields
    from the env config (or ``DEFAULT_GITHUB_MCP_MODE`` for ``mode`` when no
    env value is available) and returns it.
    """
    env_config = github_mcp_config_from_env()
    if not _has_explicit_github_mcp_overrides(
        github_url, github_mode, github_token, github_command, github_args
    ):
        return env_config
    return build_github_mcp_config(
        {
            "url": github_url or (env_config.url if env_config else ""),
            "mode": github_mode or (env_config.mode if env_config else DEFAULT_GITHUB_MCP_MODE),
            "auth_token": github_token or (env_config.auth_token if env_config else ""),
            "command": github_command or (env_config.command if env_config else ""),
            "args": github_args or (list(env_config.args) if env_config else []),
            "headers": env_config.headers if env_config else {},
            "toolsets": env_config.toolsets if env_config else (),
        }
    )
