"""Constants for the user-configured PostHog integration."""

from __future__ import annotations

from typing import Final

POSTHOG_HOST: Final[str] = "https://us.i.posthog.com"

DEFAULT_POSTHOG_URL: Final[str] = POSTHOG_HOST
DEFAULT_POSTHOG_TIMEOUT_SECONDS: Final[float] = 15.0

# --- The user's PostHog integration ---------------------------------------
POSTHOG_BASE_URL_ENV: Final[str] = "POSTHOG_BASE_URL"
POSTHOG_PROJECT_ID_ENV: Final[str] = "POSTHOG_PROJECT_ID"
POSTHOG_PERSONAL_API_KEY_ENV: Final[str] = "POSTHOG_PERSONAL_API_KEY"
POSTHOG_TIMEOUT_SECONDS_ENV: Final[str] = "POSTHOG_TIMEOUT_SECONDS"
