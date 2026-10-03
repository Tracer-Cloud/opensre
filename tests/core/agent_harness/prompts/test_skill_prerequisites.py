"""Host-owned skill prerequisites: every demo is covered, and gated skills say how to recover."""

from __future__ import annotations

import core.agent_harness.prompts.skills as skills
from config.constants import CONNECT_INTEGRATIONS_HEADING
from config.constants.github import GITHUB_SETUP_SLASH_INVOKE
from config.constants.skill_prerequisites import SKILL_PREREQUISITES
from config.constants.skills import (
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    CONNECTING_SLACK_SKILL_NAME,
    DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME,
    ONBOARDING_SKILL_NAME,
    SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME,
)
from infrastructure.harness_providers import registered_skill_prerequisite_checks


def test_every_demo_has_a_prerequisite_row_whose_checks_are_registered() -> None:
    """A new demo must decide its setup, and a table row must name a real skill and check.

    An unregistered check fails open, so a typo in a check id would silently
    remove the gate rather than break anything visible.
    """
    names = {skill.name for skill in skills.list_action_skills()}
    demos = {skill.name for skill in skills.getting_started_skills()}

    assert demos | {ONBOARDING_SKILL_NAME} <= set(SKILL_PREREQUISITES)
    assert set(SKILL_PREREQUISITES) <= names
    checks = {item.check for items in SKILL_PREREQUISITES.values() for item in items}
    assert checks
    assert checks <= set(registered_skill_prerequisite_checks())


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
