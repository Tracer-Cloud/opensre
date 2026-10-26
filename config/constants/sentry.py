"""Sentry constants — OpenSRE's own error monitoring, and the Sentry integration.

Two unrelated things share the vendor name. The values below configure the
Sentry SDK that reports *OpenSRE's* crashes to the project's own account; the
``*_ENV`` names further down identify the credentials a *user* supplies to let
agent tools query *their* Sentry. Nothing is shared between them.
"""

from __future__ import annotations

from typing import Final

SENTRY_DSN: Final[str] = (
    "https://06d6b2b739eb2267864d12c6cad34e70"
    "@o4509281671380992.ingest.us.sentry.io/4511150863482880"
)
SENTRY_ERROR_SAMPLE_RATE: Final[float] = 1.0
SENTRY_TRACES_SAMPLE_RATE: Final[float] = 1.0
SENTRY_MAX_BREADCRUMBS: Final[int] = 100
SENTRY_IN_APP_INCLUDE: Final[tuple[str, ...]] = ("app",)

# --- The user's Sentry integration ---------------------------------------
# Mirror the ``base_url`` and ``organization_slug`` credentials; the names
# deliberately differ.
SENTRY_BASE_URL_ENV: Final[str] = "SENTRY_URL"
SENTRY_ORGANIZATION_SLUG_ENV: Final[str] = "SENTRY_ORG_SLUG"
SENTRY_AUTH_TOKEN_ENV: Final[str] = "SENTRY_AUTH_TOKEN"
SENTRY_PROJECT_SLUG_ENV: Final[str] = "SENTRY_PROJECT_SLUG"
SENTRY_STATS_PERIOD_ENV: Final[str] = "SENTRY_STATS_PERIOD"
DEFAULT_SENTRY_BASE_URL: Final[str] = "https://sentry.io"
