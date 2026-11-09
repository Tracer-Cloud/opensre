"""MCP-backed onboarding integration validators."""

from __future__ import annotations

from integrations.github import (
    build_github_mcp_config,
    format_github_mcp_validation_cli_report,
    validate_github_mcp_config,
)
from integrations.posthog_mcp import (
    build_posthog_mcp_config,
    validate_posthog_mcp_config,
)
from integrations.sentry_mcp import (
    build_sentry_mcp_config,
    validate_sentry_mcp_config,
)

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


def validate_posthog_mcp_integration(
    *,
    url: str = "",
    mode: str,
    auth_token: str = "",
    command: str = "",
    args: list[str] | None = None,
    organization_id: str = "",
    project_id: str = "",
    features: list[str] | None = None,
    read_only: bool = True,
) -> IntegrationHealthResult:
    """Validate PostHog MCP connectivity by listing available tools."""
    try:
        config = build_posthog_mcp_config(
            {
                "url": url,
                "mode": mode,
                "auth_token": auth_token,
                "command": command,
                "args": args or [],
                "organization_id": organization_id,
                "project_id": project_id,
                "features": features or [],
                "read_only": read_only,
            }
        )
        result = validate_posthog_mcp_config(config)
        return IntegrationHealthResult(ok=result.ok, detail=result.detail)
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"PostHog MCP validation failed: {err}")


def validate_sentry_mcp_integration(
    *,
    url: str = "",
    mode: str,
    auth_token: str = "",
    command: str = "",
    args: list[str] | None = None,
    host: str = "",
    organization_slug: str = "",
    project_slug: str = "",
    skills: list[str] | None = None,
) -> IntegrationHealthResult:
    """Validate Sentry MCP connectivity by listing available tools."""
    try:
        config = build_sentry_mcp_config(
            {
                "url": url,
                "mode": mode,
                "auth_token": auth_token,
                "command": command,
                "args": args or [],
                "host": host,
                "organization_slug": organization_slug,
                "project_slug": project_slug,
                "skills": skills or [],
            }
        )
        result = validate_sentry_mcp_config(config)
        return IntegrationHealthResult(ok=result.ok, detail=result.detail)
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"Sentry MCP validation failed: {err}")
