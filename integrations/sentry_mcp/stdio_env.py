"""Environment for a stdio Sentry MCP server process."""

from __future__ import annotations

from collections.abc import Sequence


def sentry_stdio_env(auth_token: str, host: str, skills: Sequence[str]) -> dict[str, str]:
    """Environment the stdio Sentry MCP server needs: token, host and enabled skills."""
    return {
        **({"SENTRY_ACCESS_TOKEN": auth_token} if auth_token else {}),
        **({"SENTRY_HOST": host} if host else {}),
        **({"MCP_SKILLS": ",".join(skills)} if skills else {}),
    }


__all__ = ["sentry_stdio_env"]
