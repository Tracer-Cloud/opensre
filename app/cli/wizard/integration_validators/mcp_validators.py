"""MCP-backed onboarding integration validators."""

from __future__ import annotations

from app.integrations.github_mcp import (
    build_github_mcp_config,
    format_github_mcp_validation_cli_report,
    validate_github_mcp_config,
)
from app.integrations.openclaw import build_openclaw_config, validate_openclaw_config

from .shared import IntegrationHealthResult


def validate_github_mcp_integration(
    *,
    url: str = "",
    mode: str,
    auth_token: str = "",
    command: str = "",
    args: list[str] | None = None,
    toolsets: list[str] | None = None,
    repo_view: str = "auto",
    repo_visibility: str = "any",
) -> IntegrationHealthResult:
    """Validate GitHub MCP connectivity and required repository tools."""
    config = build_github_mcp_config(
        {
            "url": url,
            "mode": mode,
            "auth_token": auth_token,
            "command": command,
            "args": args or [],
            "toolsets": toolsets or [],
        }
    )
    result = validate_github_mcp_config(
        config,
        repo_view=repo_view,  # type: ignore[arg-type]
        repo_visibility=repo_visibility,  # type: ignore[arg-type]
    )
    return IntegrationHealthResult(
        ok=result.ok,
        detail=format_github_mcp_validation_cli_report(result),
        github_mcp=result,
    )


def validate_openclaw_integration(
    *,
    url: str = "",
    mode: str,
    auth_token: str = "",
    command: str = "",
    args: list[str] | None = None,
) -> IntegrationHealthResult:
    """Validate OpenClaw MCP connectivity by listing available tools."""
    try:
        config = build_openclaw_config(
            {
                "url": url,
                "mode": mode,
                "auth_token": auth_token,
                "command": command,
                "args": args or [],
            }
        )
        result = validate_openclaw_config(config)
        return IntegrationHealthResult(ok=result.ok, detail=result.detail)
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"OpenClaw validation failed: {err}")
