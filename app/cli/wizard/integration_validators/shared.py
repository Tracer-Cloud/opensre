"""Shared models for onboarding integration health validators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.integrations.github_mcp import GitHubMCPValidationResult


@dataclass(frozen=True)
class IntegrationHealthResult:
    """Result of validating an optional integration."""

    ok: bool
    detail: str
    github_mcp: GitHubMCPValidationResult | None = None
