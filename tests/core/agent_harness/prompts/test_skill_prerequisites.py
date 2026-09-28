"""Onboarding skills tell a fresh install how to connect GitHub before their tools."""

from __future__ import annotations

import core.agent_harness.prompts.skills as skills
from config.constants.skill_prerequisites import CONNECT_INTEGRATIONS_HEADING
from config.constants.skills import (
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    CONNECTING_SLACK_SKILL_NAME,
    DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME,
    ONBOARDING_SKILL_NAME,
    SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME,
)


def test_local_github_onboarding_checks_the_integration_before_its_tools() -> None:
    for name in (
        ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
        SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME,
    ):
        body = skills.load_skill_body(name)
        assert body.startswith(CONNECT_INTEGRATIONS_HEADING)
        assert "integrations verify github" in body
        assert "slash_invoke" in body
        assert "/integrations setup github" in body
        assert "/integrations setup <service>" in body


def test_analysis_step_retries_the_chosen_repository_after_setup() -> None:
    body = skills.load_skill_body(ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME)
    resume = body.split("### 3. Collect and compute the metrics", 1)[0]

    assert "analyze_github_ci_reliability" in resume
    assert "opensre integrations setup github" in resume
    assert "same owner, repo, and days" in resume
    assert "not a coverage gap" in resume
    scheduling = skills.load_skill_body(SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME)
    assert "analyze_github_ci_reliability" not in scheduling


def test_other_onboarding_skills_keep_their_own_setup_path() -> None:
    slack = skills.load_skill_body(CONNECTING_SLACK_SKILL_NAME)
    delegated = skills.load_skill_body(DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME)
    master = skills.load_skill_body(ONBOARDING_SKILL_NAME)

    assert CONNECT_INTEGRATIONS_HEADING not in slack
    assert "integrations verify slack" in slack
    assert CONNECT_INTEGRATIONS_HEADING not in delegated
    assert CONNECT_INTEGRATIONS_HEADING not in master
