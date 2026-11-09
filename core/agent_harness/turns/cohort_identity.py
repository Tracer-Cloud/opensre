"""Product cohort / signup-retention identity — host protocol, not vendor dialect.

Used by the unformed-metric floor and SessionGoal evaluate for *any* preferred
analytics source. Integrations own query dialects and observation parsers; this
module owns when a goal is about people-cohort identity and when a reply left
that identity open.
"""

from __future__ import annotations

# Exact stop phrase gather recipes may emit so the host floor / SessionGoal
# evaluate can key off a structured signal (any analytics integration).
COHORT_IDENTITY_UNVERIFIED_MARK = "signup event unverified"

_SIGNUP_GOAL_MARKERS = (
    "signed up",
    "sign up",
    "sign-up",
    "signup",
)

_RETENTION_MARKER = "retention"

# Retention is a storage window in most of this product's vocabulary — log,
# data and backup retention. Only a people-shaped question is about signups.
_USER_COHORT_MARKERS = (
    "user",
    "cohort",
    "person",
)

# Login / sign-in must never stand in for signup (product rule, every vendor).
_LOGIN_EVENT_MARKERS = (
    "signed_in",
    "sign_in",
    "login",
    "user_signed_in",
)

_UNVERIFIED_REPLY_MARKERS = (
    "unverified",
    "unavailable",
    "unconfirmed",
    "recognized none",
    "could not identify",
    "cannot return a count",
    "cannot provide a retention",
    "cannot be calculated",
    "no eligible",
    "no signup",
    "no rows for event",
)


def goal_needs_cohort_identity(condition: str) -> bool:
    """True when an attached SessionGoal condition is about signup/retention."""
    text = (condition or "").casefold()
    if any(marker in text for marker in _SIGNUP_GOAL_MARKERS):
        return True
    if _RETENTION_MARKER not in text:
        return False
    return any(marker in text for marker in _USER_COHORT_MARKERS)


def reply_reports_cohort_unverified(text: str) -> bool:
    """True when the assistant reported that cohort identity is unresolved."""
    lower = (text or "").casefold()
    if COHORT_IDENTITY_UNVERIFIED_MARK in lower:
        return True
    if any(marker in lower for marker in _LOGIN_EVENT_MARKERS) and "signup" in lower:
        return True
    if "signup" in lower or "signed up" in lower or "retention" in lower:
        return any(marker in lower for marker in _UNVERIFIED_REPLY_MARKERS)
    return False


__all__ = [
    "COHORT_IDENTITY_UNVERIFIED_MARK",
    "goal_needs_cohort_identity",
    "reply_reports_cohort_unverified",
]
