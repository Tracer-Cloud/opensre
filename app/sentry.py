"""Sentry SDK initialisation for runtime error monitoring.

Initialises Sentry when SENTRY_DSN is set.  Call ``init_sentry()`` once early
in each process entry-point (CLI, LangGraph worker, etc.).  Repeated calls are
safe — the function is idempotent.
"""

from __future__ import annotations

import os

_initialised = False


def init_sentry() -> None:
    """Configure and start the Sentry SDK if a DSN is available.

    The DSN is read from the ``SENTRY_DSN`` environment variable.  When the
    variable is absent or empty, this function is a no-op so that local
    development works without a Sentry project.
    """
    global _initialised  # noqa: PLW0603
    if _initialised:
        return

    dsn = os.getenv("SENTRY_DSN", "")
    if not dsn:
        _initialised = True
        return

    import sentry_sdk

    from app.config import get_environment
    from app.version import get_version

    sentry_sdk.init(
        dsn=dsn,
        environment=get_environment().value,
        release=f"opensre@{get_version()}",
        send_default_pii=True,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.2")),
    )

    _initialised = True
