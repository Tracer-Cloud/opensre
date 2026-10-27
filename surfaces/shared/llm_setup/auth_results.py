"""Shared result types for API-key and subscription authentication."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class SelectionError(ValueError):
    """An invalid or incomplete model selection that must not be persisted."""


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


@dataclass(frozen=True)
class SelectionResult:
    """The committed provider and model, without credential material."""

    provider: str
    model: str
    env_path: Path
    toolcall_model: str | None = None
