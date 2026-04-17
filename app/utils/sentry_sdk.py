"""Sentry SDK initialisation for runtime error monitoring.

Initialises Sentry when SENTRY_DSN is set.  Call ``init_sentry()`` once early
in each process entry-point (CLI, LangGraph worker, etc.).  Repeated calls are
safe — the function is idempotent.
"""

from __future__ import annotations

import os
from functools import cache


@cache
def _init_sentry_once(
    dsn: str,
    environment: str,
    release: str,
    traces_sample_rate: float,
) -> None:
    """Initialize Sentry once per effective runtime configuration."""
    import sentry_sdk  # type: ignore[import-not-found]

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        send_default_pii=True,
        traces_sample_rate=traces_sample_rate,
    )


def init_sentry() -> None:
    """Configure and start the Sentry SDK if a DSN is available.

    The DSN is read from the ``SENTRY_DSN`` environment variable.  When the
    variable is absent or empty, this function is a no-op so that local
    development works without a Sentry project.
    """
    dsn = os.getenv("SENTRY_DSN", "")
    if not dsn:
        return

    from app.config import get_environment
    from app.version import get_version

    _init_sentry_once(
        dsn=dsn,
        environment=get_environment().value,
        release=f"opensre@{get_version()}",
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.2")),
    )
