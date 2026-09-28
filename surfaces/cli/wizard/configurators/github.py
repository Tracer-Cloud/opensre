"""Onboarding adapter for the shared guided GitHub setup."""

from __future__ import annotations

from config.env_file import PROJECT_ENV_PATH
from integrations.github import (
    DEFAULT_GITHUB_MCP_MODE,
    DEFAULT_GITHUB_MCP_URL,
    setup_github,
)

__all__ = [
    "DEFAULT_GITHUB_MCP_MODE",
    "DEFAULT_GITHUB_MCP_URL",
    "_configure_github_mcp",
]


def _configure_github_mcp() -> tuple[str, str]:
    """Run the same browser sign-in and verified setup used by the CLI and shell."""
    try:
        setup_github()
    except SystemExit as exc:
        if exc.code:
            raise KeyboardInterrupt from None
    return "GitHub MCP", str(PROJECT_ENV_PATH)
