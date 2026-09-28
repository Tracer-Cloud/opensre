"""Onboarding skills tell a fresh install how to open GitHub setup."""

from __future__ import annotations

import core.agent_harness.prompts.skills as skills
from config.constants import CONNECT_INTEGRATIONS_HEADING
from config.constants.github import GITHUB_SETUP_SLASH_INVOKE
from config.constants.skills import (
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    CONNECTING_SLACK_SKILL_NAME,
    DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME,
    ONBOARDING_SKILL_NAME,
    SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME,
)


def test_local_github_onboarding_opens_setup_without_waiting_on_mcp() -> None:
    for name in (
        ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
        SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME,
    ):
        body = skills.load_skill_body(name)
        assert body.startswith(CONNECT_INTEGRATIONS_HEADING)
        assert GITHUB_SETUP_SLASH_INVOKE in body
        assert "does not block them" in body
        assert "only after verify reports" not in body
        assert "slash_invoke with `/integrations setup github`" not in body
        assert 'args=["setup", "<service>"]' in body


def test_analysis_card_is_not_contradicted_by_the_prepended_check() -> None:
    body = skills.load_skill_body(ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME)

    assert "not a coverage gap" not in body
    assert "same owner, repo, and days" not in body
    scheduling = skills.load_skill_body(SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME)
    assert "analyze_github_ci_reliability" not in scheduling


def test_connect_heading_is_exported_from_config_constants() -> None:
    assert CONNECT_INTEGRATIONS_HEADING == "## Connect integrations first"


def test_other_onboarding_skills_keep_their_own_setup_path() -> None:
    slack = skills.load_skill_body(CONNECTING_SLACK_SKILL_NAME)
    delegated = skills.load_skill_body(DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME)
    master = skills.load_skill_body(ONBOARDING_SKILL_NAME)

    assert CONNECT_INTEGRATIONS_HEADING not in slack
    assert "integrations verify slack" in slack
    assert CONNECT_INTEGRATIONS_HEADING not in delegated
    assert CONNECT_INTEGRATIONS_HEADING not in master
