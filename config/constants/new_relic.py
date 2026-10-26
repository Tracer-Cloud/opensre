"""New Relic environment variable names and NerdGraph operational constants.

The two numeric constants below are split by ownership: the timeout and the
result-limit ceiling are New Relic's own operational limits (verified against
official NerdGraph docs, 2026-08-11), while the default incident limit and
window are OpenSRE's own choices, sized for LLM context (NFR-8) rather than
the API's ceiling.
"""

from __future__ import annotations

from typing import Final

NEW_RELIC_API_KEY_ENV: Final[str] = "NEW_RELIC_API_KEY"
NEW_RELIC_ACCOUNT_ID_ENV: Final[str] = "NEW_RELIC_ACCOUNT_ID"
# Mirrors the ``base_url`` credential; the names deliberately differ.
NEW_RELIC_BASE_URL_ENV: Final[str] = "NEW_RELIC_API_URL"
NEW_RELIC_INSTANCES_ENV: Final[str] = "NEW_RELIC_INSTANCES"

#: New Relic's own documented NerdGraph REST hosts (US, EU, JP). ``base_url``
#: is rejected outside this set — the API key is sent as a header on every
#: request, so an arbitrary host would exfiltrate it (see ``config.py``).
NEW_RELIC_ALLOWED_BASE_URLS: Final[frozenset[str]] = frozenset(
    {
        "https://api.newrelic.com",
        "https://api.eu.newrelic.com",
        "https://api.jp.newrelic.com",
    }
)

#: NerdGraph's own NRQL-via-API timeout — not our choice, the vendor's limit.
NEW_RELIC_NRQL_TIMEOUT_SECONDS: Final[int] = 5
#: NerdGraph's own ceiling for a single NRQL ``LIMIT`` clause.
NEW_RELIC_NRQL_LIMIT_MAX: Final[int] = 5_000
#: NRQL's own ceiling on the number of buckets a ``TIMESERIES`` query returns.
NEW_RELIC_TIMESERIES_MAX_BUCKETS: Final[int] = 366

#: OpenSRE's own default incident-page size, well below the API ceiling.
NEW_RELIC_DEFAULT_INCIDENT_LIMIT: Final[int] = 100
#: OpenSRE's own default lookback window for alert queries.
NEW_RELIC_DEFAULT_WINDOW_MINUTES: Final[int] = 60

__all__ = [
    "NEW_RELIC_ACCOUNT_ID_ENV",
    "NEW_RELIC_ALLOWED_BASE_URLS",
    "NEW_RELIC_API_KEY_ENV",
    "NEW_RELIC_BASE_URL_ENV",
    "NEW_RELIC_DEFAULT_INCIDENT_LIMIT",
    "NEW_RELIC_DEFAULT_WINDOW_MINUTES",
    "NEW_RELIC_INSTANCES_ENV",
    "NEW_RELIC_NRQL_LIMIT_MAX",
    "NEW_RELIC_NRQL_TIMEOUT_SECONDS",
    "NEW_RELIC_TIMESERIES_MAX_BUCKETS",
]
