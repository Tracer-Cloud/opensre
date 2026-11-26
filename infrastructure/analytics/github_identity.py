"""GitHub identity for analytics events."""

from __future__ import annotations

from infrastructure.analytics.provider import get_analytics
from infrastructure.observability.errors.sentry import capture_exception


def identify_github_username(username: str) -> None:
    """Attach the authenticated GitHub username to product analytics.

    Emits an identity control and stores ``github_username`` as a persistent
    property so subsequent events carry it directly and can filter on
    ``properties.github_username`` without an identity-profile join.

    No-op for an empty username. Best-effort: telemetry kill-switches make the
    underlying calls no-ops, and any unexpected error is swallowed to Sentry.
    """
    if not username:
        return
    try:
        analytics = get_analytics()
        analytics.identify({"github_username": username})
        analytics.set_persistent_property("github_username", username)
    except Exception as exc:
        capture_exception(exc)
