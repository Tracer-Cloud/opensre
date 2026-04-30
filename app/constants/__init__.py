"""Application-wide constants: prompts, limits, identifiers, and filesystem paths."""

from __future__ import annotations

from pathlib import Path

from app.constants.posthog import (
    DEFAULT_POSTHOG_BOUNCE_THRESHOLD,
    DEFAULT_POSTHOG_BOUNCE_WINDOW,
    DEFAULT_POSTHOG_TIMEOUT_SECONDS,
    DEFAULT_POSTHOG_URL,
    POSTHOG_CAPTURE_API_KEY,
    POSTHOG_HOST,
)

TRACER_HOME_DIR: Path = Path.home() / ".tracer"
INTEGRATIONS_STORE_PATH: Path = TRACER_HOME_DIR / "integrations.json"

OPENSRE_HOME_DIR: Path = Path.home() / ".opensre"

__all__ = [
    "DEFAULT_POSTHOG_BOUNCE_THRESHOLD",
    "DEFAULT_POSTHOG_BOUNCE_WINDOW",
    "DEFAULT_POSTHOG_TIMEOUT_SECONDS",
    "DEFAULT_POSTHOG_URL",
    "INTEGRATIONS_STORE_PATH",
    "OPENSRE_HOME_DIR",
    "POSTHOG_CAPTURE_API_KEY",
    "POSTHOG_HOST",
    "TRACER_HOME_DIR",
]
