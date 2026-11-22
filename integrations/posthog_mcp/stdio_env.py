"""Environment for a stdio PostHog MCP server process."""

from __future__ import annotations


def posthog_stdio_env(auth_token: str) -> dict[str, str]:
    """Environment the stdio PostHog MCP server needs to authenticate."""
    if not auth_token:
        return {}
    return {"POSTHOG_AUTH_HEADER": f"Bearer {auth_token}", "POSTHOG_PERSONAL_API_KEY": auth_token}


__all__ = ["posthog_stdio_env"]
