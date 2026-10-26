"""Traffic classification helpers for analytics events."""

from __future__ import annotations

import os

from config.constants.analytics import ANALYTICS_ENV_ENV, ANALYTICS_TEST_ENV
from config.constants.environment import DEPLOYMENT_ENV_ENV
from infrastructure.analytics.analytics_runtime import is_ci_environment


def is_test_run() -> bool:
    """Return True when the current process should be tagged as test traffic."""
    if os.getenv(ANALYTICS_TEST_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return True

    if os.getenv("PYTEST_CURRENT_TEST"):
        return True

    return is_ci_environment()


def resolve_environment_tag() -> str:
    """Resolve coarse environment classification for analytics slicing."""
    raw = (
        (
            os.getenv(ANALYTICS_ENV_ENV)
            or os.getenv(DEPLOYMENT_ENV_ENV)
            or os.getenv("ENVIRONMENT")
            or ""
        )
        .strip()
        .lower()
    )
    if raw in {"prod", "production"}:
        return "prod"
    if raw in {"stage", "staging"}:
        return "staging"
    if raw in {"dev", "development", "local"}:
        return "dev"
    return "unknown"
