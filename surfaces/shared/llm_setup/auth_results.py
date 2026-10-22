"""Shared result types for API-key and subscription authentication."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AuthStatus:
    """Status row for one provider auth path."""

    provider: str
    label: str
    authenticated: bool
    source: str
    detail: str
    verified: bool = False
    stale: bool = False


@dataclass(frozen=True)
class AuthSetupResult:
    """Result from configuring a provider auth path."""

    provider: str
    model: str
    source: str
    detail: str
    env_path: Path | None
