"""Shared PostHog constants used across analytics and integrations."""

from __future__ import annotations

from typing import Final

POSTHOG_HOST: Final[str] = "https://us.i.posthog.com"
POSTHOG_CAPTURE_API_KEY: Final[str] = "phc_zutpVhmQw7oUmMkbawKNdYCKQWjpfASATtf5ywB75W2"

DEFAULT_POSTHOG_URL: Final[str] = POSTHOG_HOST
DEFAULT_POSTHOG_TIMEOUT_SECONDS: Final[float] = 15.0
DEFAULT_POSTHOG_BOUNCE_THRESHOLD: Final[float] = 0.6
DEFAULT_POSTHOG_BOUNCE_WINDOW: Final[str] = "24h"
