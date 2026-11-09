"""Product cohort / signup-retention SessionGoal markers (all analytics sources)."""

from __future__ import annotations

import pytest

from core.agent_harness.turns.cohort_identity import (
    goal_needs_cohort_identity,
    reply_reports_cohort_unverified,
)


@pytest.mark.parametrize(
    "condition",
    [
        "how many users signed up last week",
        "signup count for windows users",
        "d7 retention for users who signed up in march",
        "retention by user cohort",
    ],
)
def test_signup_and_user_cohort_conditions_need_cohort_identity(condition: str) -> None:
    assert goal_needs_cohort_identity(condition) is True


@pytest.mark.parametrize(
    "condition",
    [
        "data retention window for logs",
        "log retention 30 days",
        "what is our backup retention policy",
        "improve employee retention",
    ],
)
def test_storage_retention_questions_do_not_need_cohort_identity(condition: str) -> None:
    assert goal_needs_cohort_identity(condition) is False


def test_host_stop_phrase_reports_cohort_unverified() -> None:
    reply = "signup event unverified — cannot provide a retention percentage."
    assert reply_reports_cohort_unverified(reply) is True
